"""MCP server: lets a coding agent check its own change and repair it from the findings.

Two tools, both thin wrappers over `engine.run_check` (one pipeline):
- `verify_change` (local/stdio only): checks a project on the agent's machine, optionally a diff.
- `verify_files` (always): checks file contents sent in the request, written to a temp dir.
  This is the only tool a remote (HTTP / Cloud Run) server exposes, so a caller can never
  make the server read its own filesystem.

Run: `vericode-mcp` (stdio) or `vericode-mcp --transport http --host 0.0.0.0 --port 8080`.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from vericode import __version__
from vericode.config import ConfigError
from vericode.engine import run_check

MAX_FILES = 500
MAX_TOTAL_BYTES = 3 * 1024 * 1024  # stays under the HTTP transport's 4 MiB body limit

INSTRUCTIONS = (
    "VeriCode checks Python code written by AI agents for made-up packages and versions "
    "(facts come from PyPI, never from a model). Call it after you add or change imports or "
    "dependency files. verdict BLOCK: fix every BLOCK finding using its fix_hint, then call it "
    "again. WARN: review, then tell the user. PASS: done."
)

_RESULT_DOC = (
    "Returns {verdict: PASS|WARN|BLOCK, findings: [{check, severity, file, line, package, "
    "reason, fix_hint}], stats}."
)


def build_server(*, local: bool = True) -> MCPServer:
    """Build the server. `local=False` (remote HTTP) leaves out `verify_change`."""
    server = MCPServer(
        name="vericode",
        title="VeriCode",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level="WARNING",  # INFO logs every PyPI URL, i.e. the caller's package names
    )

    if local:

        @server.tool(
            description=(
                "Check a Python project on this machine for made-up packages, bad version pins "
                "and risky dependencies. Pass `root` (absolute project path). Pass `diff` (output "
                "of `git diff HEAD`) to report only problems on lines you added, or `paths` "
                "(relative to root) to limit the scan. " + _RESULT_DOC
            )
        )
        async def verify_change(
            root: str, diff: str | None = None, paths: list[str] | None = None
        ) -> dict[str, Any]:
            root_path = Path(root).expanduser()
            if not root_path.is_dir():
                raise ToolError(f"root is not a directory: {root}")
            selected = [Path(p) for p in paths] if paths else None
            return await _check(root_path, selected, diff)

    @server.tool(
        description=(
            "Check Python code you send for made-up packages, bad version pins and risky "
            "dependencies. `files` maps relative POSIX paths to full file contents: include the "
            "changed .py files plus requirements*.txt / pyproject.toml so declared deps are "
            "known. Optional `diff` (unified diff against those paths) reports only problems on "
            "added lines. " + _RESULT_DOC
        )
    )
    async def verify_files(files: dict[str, str], diff: str | None = None) -> dict[str, Any]:
        _validate_files(files)
        with tempfile.TemporaryDirectory(prefix="vericode-") as tmp:
            root = Path(tmp)
            for rel, content in files.items():
                target = root / _safe_rel(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            return await _check(root, None, diff)

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    return server


async def _check(root: Path, paths: list[Path] | None, diff: str | None) -> dict[str, Any]:
    try:
        report = await run_check(root, paths, diff)
    except ConfigError as e:  # never fall back to defaults: allowlisted names could leak
        raise ToolError(f"invalid .vericode/config.yaml: {e}") from e
    except Exception as e:  # fail open: an error result, never a BLOCK
        raise ToolError(f"vericode internal error: {type(e).__name__}: {e}") from e
    return report.to_json()


def _safe_rel(rel: str) -> PurePosixPath:
    """A relative path that stays inside the temp root, or ToolError."""
    p = PurePosixPath(rel.replace("\\", "/"))
    if not rel or p.is_absolute() or ":" in rel or any(part in ("", "..") for part in p.parts):
        raise ToolError(f"invalid file path (must be relative, no '..'): {rel!r}")
    return p


def _validate_files(files: Mapping[str, str]) -> None:
    if not files:
        raise ToolError("files is empty")
    if len(files) > MAX_FILES:
        raise ToolError(f"too many files: {len(files)} (max {MAX_FILES})")
    total = sum(len(c.encode("utf-8")) for c in files.values())
    if total > MAX_TOTAL_BYTES:
        raise ToolError(f"files too large: {total} bytes (max {MAX_TOTAL_BYTES})")
    for rel in files:
        _safe_rel(rel)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vericode-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        build_server(local=True).run("stdio")
    else:
        # Stateless + JSON responses: any Cloud Run instance can answer any request.
        build_server(local=False).run(
            "streamable-http",
            host=args.host,
            port=args.port,
            stateless_http=True,
            json_response=True,
        )


if __name__ == "__main__":
    main()
