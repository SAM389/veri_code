"""PyPI lookups with a local TTL cache.

Facts here come only from PyPI. Anything we cannot confirm (timeouts, 429, 5xx) becomes
`Unknown`, which callers must treat as "could not verify" and never as "does not exist".
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from packaging.version import InvalidVersion, Version
from platformdirs import user_cache_dir

from vericode import __version__

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
USER_AGENT = f"vericode/{__version__}"

FOUND_TTL_S = 24 * 3600
NOT_FOUND_TTL_S = 3600  # short: someone could register the name at any time

_VALID_NAME = re.compile(r"^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", re.IGNORECASE)


def normalize(name: str) -> str:
    """PEP 503 normalized project name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def is_valid_name(name: str) -> bool:
    return bool(_VALID_NAME.match(name))


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class Release:
    yanked: bool  # every file of this release is yanked
    upload_time: str | None  # earliest file upload, ISO 8601


@dataclass(frozen=True)
class PackageInfo:
    name: str  # display name from PyPI
    latest_version: str
    releases: dict[str, Release] = field(default_factory=dict)
    home_page: str | None = None
    project_urls: dict[str, str] = field(default_factory=dict)

    def version_status(self, version: str) -> Literal["present", "yanked", "missing"]:
        rel = self._find(version)
        if rel is None:
            return "missing"
        return "yanked" if rel.yanked else "present"

    def _find(self, version: str) -> Release | None:
        if version in self.releases:
            return self.releases[version]
        try:
            want = Version(version)
        except InvalidVersion:
            return None
        for v, rel in self.releases.items():
            try:
                if Version(v) == want:
                    return rel
            except InvalidVersion:
                continue
        return None

    @classmethod
    def from_pypi(cls, payload: dict[str, Any]) -> PackageInfo:
        info = payload.get("info") or {}
        releases: dict[str, Release] = {}
        for version, files in (payload.get("releases") or {}).items():
            times = sorted(
                t for f in files if (t := f.get("upload_time_iso_8601") or f.get("upload_time"))
            )
            yanked = bool(files) and all(f.get("yanked") for f in files)
            releases[version] = Release(yanked=yanked, upload_time=times[0] if times else None)
        return cls(
            name=info.get("name") or "",
            latest_version=info.get("version") or "",
            releases=releases,
            home_page=info.get("home_page") or None,
            project_urls=dict(info.get("project_urls") or {}),
        )

    def to_cache(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latest_version": self.latest_version,
            "releases": {v: [r.yanked, r.upload_time] for v, r in self.releases.items()},
            "home_page": self.home_page,
            "project_urls": self.project_urls,
        }

    @classmethod
    def from_cache(cls, d: dict[str, Any]) -> PackageInfo:
        return cls(
            name=d["name"],
            latest_version=d["latest_version"],
            releases={v: Release(bool(y), t) for v, (y, t) in d["releases"].items()},
            home_page=d.get("home_page"),
            project_urls=d.get("project_urls") or {},
        )


@dataclass(frozen=True)
class NotFound:
    name: str


@dataclass(frozen=True)
class Unknown:
    name: str
    error: str


LookupResult = PackageInfo | NotFound | Unknown


# --------------------------------------------------------------------------- cache


def default_cache_dir() -> Path:
    env = os.environ.get("VERICODE_CACHE_DIR")
    return Path(env) if env else Path(user_cache_dir("vericode", appauthor=False))


class Cache:
    """A small JSON file cache: normalized name -> {status, fetched_at, data}."""

    def __init__(self, path: Path | None, now: Callable[[], float] = time.time) -> None:
        self.path = path
        self.now = now
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._entries = loaded
            except (OSError, ValueError):
                self._entries = {}  # corrupt cache: start fresh

    @classmethod
    def default(cls) -> Cache:
        return cls(default_cache_dir() / "pypi.json")

    @classmethod
    def disabled(cls) -> Cache:
        return cls(None)

    def get(self, name: str) -> PackageInfo | NotFound | None:
        if self.path is None:
            return None
        entry = self._entries.get(name)
        if not entry:
            return None
        ttl = FOUND_TTL_S if entry.get("status") == "found" else NOT_FOUND_TTL_S
        if self.now() - entry.get("fetched_at", 0) > ttl:
            return None
        try:
            if entry["status"] == "found":
                return PackageInfo.from_cache(entry["data"])
            return NotFound(name)
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, name: str, result: LookupResult) -> None:
        if self.path is None or isinstance(result, Unknown):
            return  # never cache "could not verify"
        entry: dict[str, Any] = {"fetched_at": self.now()}
        if isinstance(result, PackageInfo):
            entry |= {"status": "found", "data": result.to_cache()}
        else:
            entry["status"] = "not_found"
        self._entries[name] = entry
        self._dirty = True

    def save(self) -> None:
        if self.path is None or not self._dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh)
            os.replace(tmp, self.path)
            self._dirty = False
        except OSError:
            pass  # a cache write failure must never fail a check


# --------------------------------------------------------------------------- registry


class Registry:
    """Async PyPI client. Use as `async with Registry() as reg: await reg.lookup_many(...)`."""

    def __init__(
        self,
        cache: Cache | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        concurrency: int = 10,
        timeout_s: float = 5.0,
        retries: int = 1,
    ) -> None:
        self.cache = cache if cache is not None else Cache.default()
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout_s
        self._retries = retries
        self._sem = asyncio.Semaphore(concurrency)
        self.network_lookups = 0

    async def __aenter__(self) -> Registry:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.cache.save()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def lookup(self, name: str) -> LookupResult:
        key = normalize(name)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        result = await self._fetch(key)
        self.cache.put(key, result)
        return result

    async def lookup_many(self, names: Iterable[str]) -> dict[str, LookupResult]:
        """Look up names concurrently. Keys of the result are normalized names."""
        keys = sorted({normalize(n) for n in names})
        results = await asyncio.gather(*(self.lookup(k) for k in keys))
        return dict(zip(keys, results, strict=True))

    async def _fetch(self, key: str) -> LookupResult:
        assert self._client is not None, "use `async with Registry()`"
        if not is_valid_name(key):
            return Unknown(key, "not a valid PyPI project name")
        url = PYPI_JSON_URL.format(name=key)
        error = "unknown error"
        async with self._sem:
            for _attempt in range(self._retries + 1):
                self.network_lookups += 1
                try:
                    resp = await self._client.get(url)
                except httpx.TimeoutException:
                    error = "PyPI request timed out"
                    continue
                except httpx.HTTPError as e:
                    error = f"PyPI request failed: {type(e).__name__}"
                    continue
                if resp.status_code == 404:
                    return NotFound(key)
                if resp.status_code == 200:
                    try:
                        return PackageInfo.from_pypi(resp.json())
                    except (ValueError, TypeError, AttributeError):
                        return Unknown(key, "PyPI returned an unreadable response")
                error = f"PyPI returned HTTP {resp.status_code}"
                if resp.status_code < 500:  # 429/403/...: retrying right away won't help
                    break
        return Unknown(key, error)
