"""Single entry point used by the CLI, the MCP server and the benchmark."""

from __future__ import annotations

import time
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from vericode.config import Config, load_config
from vericode.diff import parse_unified_diff
from vericode.extract import (
    DeclaredDep,
    ImportRef,
    discover,
    extract_imports,
    is_dependency_file,
    is_requirements_file,
    parse_pyproject,
    parse_requirements,
    third_party,
)
from vericode.mapping import Resolved, Resolver
from vericode.registry import (
    Cache,
    LookupResult,
    NotFound,
    PackageInfo,
    Registry,
    Unknown,
    normalize,
)
from vericode.risk import RiskSignals, TopPackages, assess, near_miss, top_packages
from vericode.verdict import Check, Finding, Report, Severity


@dataclass
class _Collected:
    files: int = 0
    imports: list[ImportRef] = field(default_factory=list)
    deps: list[DeclaredDep] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass(frozen=True)
class _Use:
    """One import resolved to one PyPI package."""

    ref: ImportRef
    resolved: Resolved

    @property
    def package(self) -> str:
        return self.resolved.package


@dataclass(frozen=True)
class _Declared:
    """What the project declares as dependencies, for mapping and the undeclared check."""

    names: frozenset[str]  # normalized
    files: tuple[str, ...]  # dependency files that exist, relative to root

    @property
    def exists(self) -> bool:
        return bool(self.files)


async def run_check(
    root: Path,
    paths: Sequence[Path] | None = None,
    diff: str | None = None,
    *,
    registry: Registry | None = None,
    use_cache: bool = True,
    config: Config | None = None,
) -> Report:
    """Check files (or the files touched by a unified diff) under `root`.

    In diff mode only findings on added lines are reported, plus could-not-verify notices.
    Raises `config.ConfigError` if `.vericode/config.yaml` is invalid.
    """
    started = time.perf_counter()
    root = root.resolve()
    config = config if config is not None else load_config(root)

    added: dict[str, set[int]] | None = None
    if diff is not None:
        added = parse_unified_diff(diff)
        files = [root / p for p in added if _checkable(root / p)]
    else:
        files = list(discover([p if p.is_absolute() else root / p for p in (paths or [root])]))
    files = [f for f in files if not config.is_ignored(_rel(f, root))]

    collected = _collect(files, root)
    declared = _declared(root, collected.deps)
    resolver = Resolver(root, declared=declared.names, allow=config.allow)

    uses = [_Use(ref, r) for ref in collected.imports for r in resolver.resolve(ref)]
    deps = [d for d in collected.deps if not config.is_allowed(d.name)]
    names = {u.package for u in uses} | {normalize(d.name) for d in deps}

    top = top_packages()
    reg = registry or Registry(Cache.default() if use_cache else Cache.disabled())
    async with reg:
        results = await reg.lookup_many(names)
        # Risk signals only matter for packages that exist and are not already popular.
        unpopular = sorted(n for n, r in results.items() if _exists(r) and n not in top)
        downloads = await reg.downloads_many(unpopular) if unpopular else {}

    findings = collected.findings
    findings += _import_findings(uses, results, top)
    findings += _dep_findings(deps, results, top)
    findings += _risk_findings(
        unpopular, uses, deps, results, downloads, top, added, datetime.now(UTC)
    )
    findings += _undeclared_findings(uses, results, declared, added)
    if added is not None:
        findings = [f for f in findings if _on_added_line(f, added)]

    return Report(
        findings=findings,
        files_checked=collected.files,
        packages_checked=len(names),
        duration_s=time.perf_counter() - started,
    )


def _exists(res: LookupResult | None) -> bool:
    return isinstance(res, PackageInfo)


def _checkable(path: Path) -> bool:
    return path.is_file() and (path.suffix == ".py" or is_dependency_file(path))


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _collect(files: Sequence[Path], root: Path) -> _Collected:
    out = _Collected()
    for f in files:
        rel = _rel(f, root)
        out.files += 1
        try:
            if f.suffix == ".py":
                out.imports += third_party(extract_imports(f, rel))
            elif f.name == "pyproject.toml":
                out.deps += parse_pyproject(f, rel)
            else:
                out.deps += parse_requirements(f, rel)
        except SyntaxError as e:
            out.findings.append(_unparsable(rel, e.lineno, f"Python syntax error: {e.msg}"))
        except tomllib.TOMLDecodeError as e:
            out.findings.append(_unparsable(rel, None, f"Invalid TOML: {e}"))
        except (OSError, ValueError) as e:
            out.findings.append(_unparsable(rel, None, f"Could not read file: {e}"))
    return out


def _declared(root: Path, scanned: Sequence[DeclaredDep]) -> _Declared:
    """Declared deps from the scanned files plus the root's own dependency files, so diff mode
    and single-file checks still know what the project declares. Root files are read quietly:
    if they are also scanned, `_collect` already reported any parse error."""
    deps = list(scanned)
    files = {d.file for d in scanned}
    root_files = [root / "pyproject.toml", *sorted(root.glob("requirements*.txt"))]
    for path in root_files:
        if not path.is_file():
            continue
        rel = _rel(path, root)
        try:
            found = (
                parse_pyproject(path, rel)
                if path.name == "pyproject.toml"
                else parse_requirements(path, rel)
            )
        except (OSError, ValueError):  # TOMLDecodeError is a ValueError
            continue
        deps += found
        if found or is_requirements_file(path):
            files.add(rel)
    return _Declared(frozenset(normalize(d.name) for d in deps), tuple(sorted(files)))


def _unparsable(rel: str, line: int | None, reason: str) -> Finding:
    return Finding(
        check=Check.COULD_NOT_VERIFY,
        severity=Severity.WARN,
        file=rel,
        line=line,
        reason=reason,
        fix_hint="Fix the file so it parses, then re-run the check.",
    )


def _stmt(ref: ImportRef) -> str:
    if ref.names:
        return f"from {ref.module} import {', '.join(ref.names)}"
    return f"import {ref.module}"


def _import_findings(
    uses: Sequence[_Use], results: dict[str, LookupResult], top: TopPackages
) -> list[Finding]:
    findings: list[Finding] = []
    unknown_reported: set[str] = set()
    for use in uses:
        ref, key = use.ref, use.package
        res = results.get(key)
        if isinstance(res, NotFound):
            reason = "Not found on PyPI (HTTP 404)"
            if use.resolved.guessed:
                reason += (
                    f" under '{key}', the name guessed for namespace import `{use.resolved.path}`"
                )
            if ref.optional:
                reason += " - optional import inside try/except or TYPE_CHECKING"
            nm = near_miss(key, top, allow_variants=True)
            if nm:
                hint = (
                    f"Did you mean '{nm.candidate}'? No package named '{key}' exists on PyPI; "
                    f"fix `{_stmt(ref)}`."
                )
            else:
                hint = (
                    f"Remove `{_stmt(ref)}`: no package named '{key}' exists on PyPI. "
                    "Use a real package or write the code without it."
                )
            findings.append(
                Finding(
                    check=Check.PACKAGE_EXISTS,
                    # A guessed name is weaker evidence than the import name itself.
                    severity=Severity.WARN
                    if ref.optional or use.resolved.guessed
                    else Severity.BLOCK,
                    file=ref.file,
                    line=ref.line,
                    package=key,
                    reason=reason,
                    fix_hint=hint,
                )
            )
        elif isinstance(res, Unknown) and key not in unknown_reported:
            unknown_reported.add(key)
            findings.append(_could_not_verify(ref.file, ref.line, key, res))
    return findings


def _dep_findings(
    deps: Sequence[DeclaredDep], results: dict[str, LookupResult], top: TopPackages
) -> list[Finding]:
    findings: list[Finding] = []
    for dep in deps:
        key = normalize(dep.name)
        res = results.get(key)
        if isinstance(res, NotFound):
            nm = near_miss(key, top, allow_variants=True)
            hint = f"Remove '{dep.name}' from {dep.file}: no package with this name exists on PyPI."
            if nm:
                hint = f"Did you mean '{nm.candidate}'? " + hint
            findings.append(
                Finding(
                    check=Check.PACKAGE_EXISTS,
                    severity=Severity.BLOCK,
                    file=dep.file,
                    line=dep.line,
                    package=key,
                    reason="Not found on PyPI (HTTP 404)",
                    fix_hint=hint,
                )
            )
        elif isinstance(res, Unknown):
            findings.append(_could_not_verify(dep.file, dep.line, key, res))
        elif isinstance(res, PackageInfo) and (pinned := dep.pinned_version):
            findings += _version_finding(dep, key, pinned, res)
    return findings


def _version_finding(dep: DeclaredDep, key: str, pinned: str, info: PackageInfo) -> list[Finding]:
    status = info.version_status(pinned)
    if status == "present":
        return []
    latest = f" Latest release is {info.latest_version}." if info.latest_version else ""
    if status == "missing":
        return [
            Finding(
                check=Check.VERSION_EXISTS,
                severity=Severity.BLOCK,
                file=dep.file,
                line=dep.line,
                package=key,
                reason=f"Version {pinned} not found in PyPI releases of '{info.name or key}'",
                fix_hint=f"Pin a version of '{dep.name}' that exists.{latest}",
            )
        ]
    return [
        Finding(
            check=Check.VERSION_EXISTS,
            severity=Severity.WARN,
            file=dep.file,
            line=dep.line,
            package=key,
            reason=f"Version {pinned} of '{info.name or key}' was yanked on PyPI",
            fix_hint=f"Pin a version of '{dep.name}' that is not yanked.{latest}",
        )
    ]


def _could_not_verify(file: str, line: int, key: str, res: Unknown) -> Finding:
    return Finding(
        check=Check.COULD_NOT_VERIFY,
        severity=Severity.WARN,
        file=file,
        line=line,
        package=key,
        reason=res.error,
        fix_hint="VeriCode could not confirm this package; re-run when PyPI is reachable.",
    )


# --------------------------------------------------------------------------- per-package checks


def _locations(
    uses: Iterable[_Use], deps: Iterable[DeclaredDep]
) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for u in uses:
        out.setdefault(u.package, []).append((u.ref.file, u.ref.line))
    for d in deps:
        out.setdefault(normalize(d.name), []).append((d.file, d.line))
    return out


def _first(locs: Sequence[tuple[str, int]], added: dict[str, set[int]] | None) -> tuple[str, int]:
    """Where to report a per-package finding: the first use, preferring added lines in diff
    mode so the finding is not filtered out when the package also appears in old code."""
    ordered = sorted(locs)
    if added is not None:
        for file, line in ordered:
            if line in added.get(file, ()):
                return file, line
    return ordered[0]


def _signal_text(s: RiskSignals) -> str:
    parts: list[str] = []
    if s.new and s.first_release:
        parts.append(f"first release {s.first_release:%Y-%m-%d} ({s.age_days} days ago)")
    if s.low_downloads:
        parts.append(f"{s.downloads_last_month} downloads last month")
    if s.single_release:
        parts.append("only 1 release")
    if not s.has_links:
        parts.append("no homepage or source URL")
    return "; ".join(parts)


def _risk_findings(
    candidates: Sequence[str],
    uses: Sequence[_Use],
    deps: Sequence[DeclaredDep],
    results: dict[str, LookupResult],
    downloads: dict[str, int | None],
    top: TopPackages,
    added: dict[str, set[int]] | None,
    now: datetime,
) -> list[Finding]:
    """One WARN per risky package: `near_miss` if the name is close to a popular package,
    otherwise `risk`. A near miss on a healthy package is not reported (e.g. httpie/httpx)."""
    findings: list[Finding] = []
    locs = _locations(uses, deps)
    for key in candidates:
        info = results.get(key)
        if not isinstance(info, PackageInfo) or key not in locs:
            continue
        signals = assess(info, downloads.get(key), now)
        if not signals.flagged:
            continue
        file, line = _first(locs[key], added)
        nm = near_miss(key, top)
        if nm:
            edits = "edit" if nm.distance == 1 else "edits"
            findings.append(
                Finding(
                    check=Check.NEAR_MISS,
                    severity=Severity.WARN,
                    file=file,
                    line=line,
                    package=key,
                    reason=f"Name is {nm.distance} {edits} from the popular package "
                    f"'{nm.candidate}', and {_signal_text(signals)}",
                    fix_hint=f"Did you mean '{nm.candidate}'? If so, fix the name. If '{key}' "
                    "is intended, confirm it is the package you want before installing it.",
                )
            )
        else:
            findings.append(
                Finding(
                    check=Check.RISK,
                    severity=Severity.WARN,
                    file=file,
                    line=line,
                    package=key,
                    reason=f"New or little-used package: {_signal_text(signals)}",
                    fix_hint=f"Confirm '{key}' is the package you intended (check its source "
                    "and maintainers) before installing it.",
                )
            )
    return findings


def _undeclared_findings(
    uses: Sequence[_Use],
    results: dict[str, LookupResult],
    declared: _Declared,
    added: dict[str, set[int]] | None,
) -> list[Finding]:
    """Required imports of real packages that no dependency file declares. Skipped when the
    project has no dependency files at all (plain scripts)."""
    if not declared.exists:
        return []
    missing: dict[str, list[tuple[str, int]]] = {}
    for u in uses:
        if u.ref.optional or u.package in declared.names or not _exists(results.get(u.package)):
            continue
        missing.setdefault(u.package, []).append((u.ref.file, u.ref.line))
    findings: list[Finding] = []
    for key, locs in sorted(missing.items()):
        info = results[key]
        assert isinstance(info, PackageInfo)
        file, line = _first(locs, added)
        findings.append(
            Finding(
                check=Check.UNDECLARED,
                severity=Severity.WARN,
                file=file,
                line=line,
                package=key,
                reason=f"Imported but not declared in {', '.join(declared.files)}",
                fix_hint=f"Add '{info.name or key}' to {declared.files[0]} so the project "
                "installs it.",
            )
        )
    return findings


def _on_added_line(f: Finding, added: dict[str, set[int]]) -> bool:
    if f.check is Check.COULD_NOT_VERIFY or f.file is None or f.file not in added:
        return True
    return f.line is None or f.line in added[f.file]
