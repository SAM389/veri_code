"""Extract imports from .py files and declared deps from requirements/pyproject."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

# Directories never worth scanning: virtualenvs, build output, caches, VCS.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        ".env",
        "site-packages",
        "build",
        "dist",
        "__pycache__",
        "node_modules",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".eggs",
    }
)

STDLIB = frozenset(sys.stdlib_module_names) | {"__future__"}

# Exceptions whose handler makes the import in the try body "optional".
_OPTIONAL_EXC = frozenset({"ImportError", "ModuleNotFoundError", "Exception", "BaseException"})


@dataclass(frozen=True)
class ImportRef:
    module: str  # full dotted name, e.g. "google.cloud.storage"
    file: str  # path relative to the check root, POSIX style
    line: int
    optional: bool = False  # inside try/except ImportError or `if TYPE_CHECKING:`

    @property
    def top_level(self) -> str:
        return self.module.split(".", 1)[0]


@dataclass(frozen=True)
class DeclaredDep:
    name: str  # as written in the file
    specifier: str  # e.g. "==2.31.0", "" if unconstrained
    file: str
    line: int

    @property
    def pinned_version(self) -> str | None:
        """The exact version if pinned with `==X` (no wildcard), else None."""
        specs = list(Requirement(f"{self.name}{self.specifier}").specifier)
        if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
            return specs[0].version
        return None


# --------------------------------------------------------------------------- imports


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self, file: str) -> None:
        self.file = file
        self.refs: list[ImportRef] = []
        self._optional_depth = 0

    def _add(self, module: str, line: int) -> None:
        self.refs.append(ImportRef(module, self.file, line, self._optional_depth > 0))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:  # skip relative imports
            self._add(node.module, node.lineno)

    def _visit_try(self, node: ast.Try | ast.TryStar) -> None:
        optional = any(_catches_import_error(h) for h in node.handlers)
        self._optional_depth += optional
        for stmt in node.body:
            self.visit(stmt)
        self._optional_depth -= optional
        for part in (*node.handlers, *node.orelse, *node.finalbody):
            self.visit(part)

    visit_Try = _visit_try
    visit_TryStar = _visit_try

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking(node.test):
            self._optional_depth += 1
            for stmt in node.body:
                self.visit(stmt)
            self._optional_depth -= 1
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _catches_import_error(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:  # bare `except:`
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(_name_of(t) in _OPTIONAL_EXC for t in types)


def _is_type_checking(test: ast.expr) -> bool:
    return _name_of(test) == "TYPE_CHECKING"


def extract_imports(path: Path, rel: str) -> list[ImportRef]:
    """All absolute imports in a file. Raises SyntaxError / ValueError on unparsable files."""
    tree = ast.parse(path.read_bytes(), filename=rel)
    visitor = _ImportVisitor(rel)
    visitor.visit(tree)
    return visitor.refs


def third_party(refs: Iterable[ImportRef]) -> list[ImportRef]:
    """Drop stdlib imports. (Local-module and allowlist filtering arrive with mapping.)"""
    return [r for r in refs if r.top_level not in STDLIB]


# --------------------------------------------------------------------------- dependency files

_COMMENT = re.compile(r"(^|\s)#.*$")


def parse_requirements(path: Path, rel: str) -> list[DeclaredDep]:
    """Parse a requirements.txt. Skips options (-r, -e, --index-url), URLs, paths, comments."""
    deps: list[DeclaredDep] = []
    pending, start = "", 0
    for lineno, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        line = _COMMENT.sub("", raw).strip()
        if not pending:
            start = lineno
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        line, pending = (pending + line).strip(), ""
        if not line or line.startswith(("-", ".", "/")) or "://" in line:
            continue
        dep = _parse_req(line, rel, start)
        if dep:
            deps.append(dep)
    return deps


def _parse_req(text: str, rel: str, line: int) -> DeclaredDep | None:
    try:
        req = Requirement(text)
    except InvalidRequirement:
        return None
    if req.url:  # direct reference (`pkg @ https://...`): not a PyPI lookup
        return None
    return DeclaredDep(req.name, str(req.specifier), rel, line)


def parse_pyproject(path: Path, rel: str) -> list[DeclaredDep]:
    """Dependencies from [project], [dependency-groups] and [tool.poetry]."""
    text = path.read_text(encoding="utf-8", errors="replace")
    data = tomllib.loads(text)
    lines = text.splitlines()
    pep508: list[str] = []

    project = data.get("project", {})
    pep508 += project.get("dependencies", [])
    for group in project.get("optional-dependencies", {}).values():
        pep508 += group
    for group in data.get("dependency-groups", {}).values():
        pep508 += [d for d in group if isinstance(d, str)]  # skip {include-group = ...}

    deps = [d for s in pep508 if (d := _parse_req(s, rel, _find_line(lines, s)))]

    poetry = data.get("tool", {}).get("poetry", {})
    poetry_tables = [poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})]
    poetry_tables += [g.get("dependencies", {}) for g in poetry.get("group", {}).values()]
    for table in poetry_tables:
        for name, spec in table.items():
            if name.lower() == "python":
                continue
            version = spec.get("version", "") if isinstance(spec, dict) else spec
            if isinstance(spec, dict) and ("path" in spec or "git" in spec or "url" in spec):
                continue
            specifier = _poetry_specifier(version)
            deps.append(DeclaredDep(name, specifier, rel, _find_line(lines, name)))
    return deps


def _poetry_specifier(version: str) -> str:
    """Only an exact Poetry version ("2.31.0" or "==2.31.0") maps to a pin we can verify."""
    v = version.strip()
    if re.fullmatch(r"=?=?\s*\d[\w.!+-]*", v):
        return "==" + v.lstrip("= ")
    return ""


def _find_line(lines: list[str], needle: str) -> int:
    """Best-effort line number (tomllib does not report positions)."""
    for i, line in enumerate(lines, 1):
        if needle in line:
            return i
    name = re.split(r"[\s\[<>=!~;@]", needle, maxsplit=1)[0]
    for i, line in enumerate(lines, 1):
        if re.search(rf"(^|[\"'\s]){re.escape(name)}([\"'\s\[<>=!~;]|$)", line):
            return i
    return 1


# --------------------------------------------------------------------------- discovery


def is_requirements_file(path: Path) -> bool:
    return path.suffix == ".txt" and path.name.lower().startswith("requirements")


def is_dependency_file(path: Path) -> bool:
    return path.name == "pyproject.toml" or is_requirements_file(path)


def discover(paths: Iterable[Path]) -> Iterator[Path]:
    """Yield .py and dependency files under the given files/directories, deduplicated."""
    seen: set[Path] = set()
    for p in paths:
        candidates = [p] if p.is_file() else _walk(p)
        for f in candidates:
            if (f.suffix == ".py" or is_dependency_file(f)) and f.resolve() not in seen:
                seen.add(f.resolve())
                yield f


def _walk(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if entry.name in SKIP_DIRS or entry.name.startswith(".") or _is_venv(entry):
                continue
            yield from _walk(entry)
        elif entry.is_file():
            yield entry


def _is_venv(path: Path) -> bool:
    return (path / "pyvenv.cfg").is_file()
