"""Bundle the Python that the web page runs in Pyodide into `web/site/vericode.zip`.

The zip holds the real `vericode` package (code + data), the browser entry point
`vericode_web.py`, and a small pure-Python `rapidfuzz` stand-in (Pyodide has no rapidfuzz).
Run before `firebase deploy` (firebase.json does this as a predeploy step).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

WEB = Path(__file__).resolve().parent
REPO = WEB.parent
OUT = WEB / "site" / "vericode.zip"

# Not needed in the browser (and their deps, typer/mcp, are not installed there).
SKIP = {"cli.py", "mcp_server.py"}


def _add_tree(zf: zipfile.ZipFile, src: Path, arc_root: Path) -> None:
    for path in sorted(src.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.name in SKIP:
            continue
        zf.write(path, (arc_root / path.relative_to(src)).as_posix())


def build() -> Path:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        _add_tree(zf, REPO / "vericode", Path("vericode"))
        _add_tree(zf, WEB / "py", Path("."))
    return OUT


if __name__ == "__main__":
    out = build()
    print(f"wrote {out.relative_to(REPO).as_posix()} ({out.stat().st_size // 1024} KB)")
