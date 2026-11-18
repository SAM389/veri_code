"""Import name -> PyPI package name, plus the filters that keep names off PyPI entirely.

`Resolver.resolve(ref)` returns the packages an import refers to, or nothing when the import
must not be looked up (stdlib, local module, allowlisted, bare namespace root). Resolution
order for each dotted path the import names:

1. installed  - `importlib.metadata.packages_distributions()` of the running environment
2. declared   - a dependency the project declares under the import name (or `py`/`python-`
                variants of it, e.g. `jwt` -> `PyJWT`)
3. map        - bundled `data/import_map.json`, longest dotted-prefix match
                (`google.cloud.storage` -> `google-cloud-storage`, `cv2` -> `opencv-python`)
4. namespace  - guessed from a known namespace (`azure.mgmt.compute` -> `azure-mgmt-compute`).
                A guess is weaker evidence, so a 404 on it is a WARN, not a BLOCK.
5. import-name - the top-level import name itself, PEP 503 normalized
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import metadata, resources
from pathlib import Path
from typing import Literal

from vericode.extract import SKIP_DIRS, STDLIB, ImportRef
from vericode.registry import normalize

Source = Literal["installed", "declared", "map", "namespace", "import-name"]

# Namespace packages whose sub-packages ship as separate distributions named
# `<namespace>-<sub>`. Longest match wins. The bare namespace itself is never looked up.
NAMESPACES: tuple[str, ...] = (
    "azure",
    "azure.ai",
    "azure.keyvault",
    "azure.mgmt",
    "azure.storage",
    "backports",
    "google",
    "google.cloud",
    "jaraco",
    "opentelemetry",
    "opentelemetry.exporter",
    "opentelemetry.instrumentation",
    "ruamel",
    "sphinxcontrib",
    "zope",
)
_NS_PARTS = tuple(tuple(ns.split(".")) for ns in NAMESPACES)
_NS_TOPS = frozenset(p[0] for p in _NS_PARTS)

_MODULE_SUFFIXES = (".py", ".pyi", ".pyd", ".so")


@dataclass(frozen=True)
class Resolved:
    package: str  # PEP 503 normalized PyPI name
    source: Source
    path: str  # the dotted import path that produced this resolution

    @property
    def guessed(self) -> bool:
        return self.source == "namespace"


@cache
def import_map() -> dict[str, str]:
    text = resources.files("vericode").joinpath("data/import_map.json").read_text("utf-8")
    return {k: normalize(v) for k, v in json.loads(text).items()}


@cache
def installed_distributions() -> dict[str, list[str]]:
    """Top-level import name -> distributions providing it, in the running environment."""
    try:
        return {k: list(v) for k, v in metadata.packages_distributions().items()}
    except Exception:  # broken metadata in some env must never break a check
        return {}


class Resolver:
    def __init__(
        self,
        root: Path,
        *,
        declared: Iterable[str] = (),
        allow: Iterable[str] = (),
        installed: Mapping[str, Sequence[str]] | None = None,
        imports: Mapping[str, str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.declared = frozenset(normalize(d) for d in declared)
        self.allow = frozenset(normalize(a) for a in allow)
        self._installed = installed
        self._map = imports
        self._dir_cache: dict[Path, frozenset[str]] = {}
        self.local = self._dir_modules(self.root) | self._dir_modules(self.root / "src")
        if own := _project_name(self.root):
            self.local |= {own}

    # ------------------------------------------------------------------ filters

    def is_local(self, ref: ImportRef) -> bool:
        """Top-level name is a module in the repo root, `src/`, or any directory between the
        importing file and the root (scripts import their siblings)."""
        top = normalize(ref.top_level)
        if top in self.local:
            return True
        d = (self.root / ref.file).parent
        while d != self.root and self.root in d.parents:
            if top in self._dir_modules(d):
                return True
            d = d.parent
        return False

    def is_allowed(self, name: str) -> bool:
        return normalize(name) in self.allow

    def skip(self, ref: ImportRef) -> bool:
        return ref.top_level in STDLIB or self.is_allowed(ref.top_level) or self.is_local(ref)

    # ------------------------------------------------------------------ resolution

    def resolve(self, ref: ImportRef) -> list[Resolved]:
        """Packages this import refers to (deduplicated). Empty when it must not be looked up."""
        if self.skip(ref):
            return []
        out: dict[str, Resolved] = {}
        for path in ref.paths:
            r = self.resolve_path(path)
            if r and not self.is_allowed(r.package) and r.package not in out:
                out[r.package] = r
        return list(out.values())

    def resolve_path(self, path: str) -> Resolved | None:
        parts = tuple(path.split("."))
        ns = _namespace(parts)
        if ns is not None and len(parts) <= len(ns):
            return None  # bare namespace root (`import google`): nothing to look up
        top = parts[0]

        if ns is None and (pkg := self._from_installed(top)):
            return Resolved(pkg, "installed", path)

        if ns is None:
            t = normalize(top)
            variants = (t, f"py{t}", f"py-{t}", f"python-{t}", f"{t}-python", f"{t}-py")
            for v in variants:
                if v in self.declared:
                    return Resolved(v, "declared", path)
        else:
            guess = normalize("-".join(parts[: len(ns) + 1]))
            if guess in self.declared:
                return Resolved(guess, "declared", path)

        mapping = self._map if self._map is not None else import_map()
        for i in range(len(parts), 0, -1):
            key = ".".join(parts[:i])
            if key in mapping:
                return Resolved(mapping[key], "map", path)

        if ns is not None:
            return Resolved(normalize("-".join(parts[: len(ns) + 1])), "namespace", path)
        return Resolved(normalize(top), "import-name", path)

    def _from_installed(self, top: str) -> str | None:
        installed = self._installed if self._installed is not None else installed_distributions()
        dists = sorted({normalize(d) for d in installed.get(top, ())})
        if not dists:
            return None
        if len(dists) == 1:
            return dists[0]
        declared = [d for d in dists if d in self.declared]
        return declared[0] if len(declared) == 1 else None  # ambiguous: fall through

    # ------------------------------------------------------------------ helpers

    def _dir_modules(self, d: Path) -> frozenset[str]:
        """Normalized names importable from directory `d` (modules and packages)."""
        if d in self._dir_cache:
            return self._dir_cache[d]
        names: set[str] = set()
        try:
            entries = list(d.iterdir()) if d.is_dir() else []
        except OSError:
            entries = []
        for e in entries:
            try:
                if e.is_file() and e.name.endswith(_MODULE_SUFFIXES):
                    names.add(normalize(e.name.split(".", 1)[0]))
                elif e.is_dir() and _is_package_dir(e):
                    names.add(normalize(e.name))
            except OSError:
                continue
        result = frozenset(names)
        self._dir_cache[d] = result
        return result


def _namespace(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    if parts[0] not in _NS_TOPS:
        return None
    best: tuple[str, ...] | None = None
    for ns in _NS_PARTS:
        if parts[: len(ns)] == ns and (best is None or len(ns) > len(best)):
            best = ns
    return best


def _is_package_dir(d: Path) -> bool:
    if d.name in SKIP_DIRS or d.name.startswith(".") or (d / "pyvenv.cfg").exists():
        return False
    if (d / "__init__.py").is_file():
        return True
    return any(f.suffix == ".py" for f in d.iterdir() if f.is_file())  # namespace package


def _project_name(root: Path) -> str | None:
    """The project's own name from pyproject.toml, so `import myproject` is never looked up."""
    path = root / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
    return normalize(name) if isinstance(name, str) and name else None
