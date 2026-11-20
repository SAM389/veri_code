"""Browser entry point: run `engine.run_check` inside Pyodide on code pasted into the web page.

The page passes `{relpath: content}`; we write the files to Pyodide's in-memory disk and run the
normal pipeline. PyPI is reached with the browser's `fetch` (PyPI's JSON API allows CORS).
pypistats does not allow CORS, so download counts are skipped (the engine treats a failed
download lookup as "no signal", per the fail-open rule).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from vericode.engine import run_check
from vericode.registry import PYPISTATS_URL, Cache, Registry

MAX_FILES = 20
MAX_TOTAL_BYTES = 500_000
FETCH_TIMEOUT_S = 8.0
_PYPISTATS_PREFIX = PYPISTATS_URL.split("{", 1)[0]

# One cache per page load, kept in Pyodide's in-memory filesystem.
_CACHE = Cache(Path(tempfile.gettempdir()) / "vericode-cache" / "pypi.json")

Fetch = Callable[[str], Awaitable[tuple[int, str]]]


class _Response:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)


async def _pyodide_fetch(url: str) -> tuple[int, str]:
    from pyodide.http import pyfetch  # only exists inside Pyodide

    resp = await pyfetch(url)
    return resp.status, await resp.text()


class FetchClient:
    """The slice of `httpx.AsyncClient` that `Registry` uses (`get`), backed by browser fetch.
    Failures are raised as httpx errors so `Registry` turns them into `Unknown` (WARN)."""

    def __init__(self, fetch: Fetch | None = None, timeout_s: float = FETCH_TIMEOUT_S) -> None:
        self._fetch = fetch or _pyodide_fetch
        self._timeout = timeout_s

    async def get(self, url: str) -> _Response:
        if url.startswith(_PYPISTATS_PREFIX):
            raise httpx.ConnectError("pypistats.org does not allow browser requests")
        try:
            status, text = await asyncio.wait_for(self._fetch(url), self._timeout)
        except TimeoutError as e:
            raise httpx.ReadTimeout("request timed out") from e
        except Exception as e:  # browser network/CORS errors surface as JS exceptions
            raise httpx.ConnectError(f"request failed: {type(e).__name__}") from e
        return _Response(status, text)


def _safe_rel(name: str) -> PurePosixPath:
    p = PurePosixPath(name.replace("\\", "/"))
    if not name or p.is_absolute() or ".." in p.parts or ":" in name:
        raise ValueError(f"unsafe file name: {name!r}")
    return p


async def check(files_json: str, fetch: Fetch | None = None) -> str:
    """`files_json` = JSON object {relpath: content}. Returns `Report.to_json()` as JSON."""
    files = json.loads(files_json)
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError(f"expected at most {MAX_FILES} files")
    if sum(len(str(c).encode()) for c in files.values()) > MAX_TOTAL_BYTES:
        raise ValueError("files are too large")
    root = Path(tempfile.mkdtemp(prefix="vericode-web-"))
    try:
        for name, content in files.items():
            path = root / _safe_rel(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
        registry = Registry(_CACHE, client=FetchClient(fetch))  # type: ignore[arg-type]
        report = await run_check(root, registry=registry)
        return json.dumps(report.to_json())
    finally:
        shutil.rmtree(root, ignore_errors=True)
