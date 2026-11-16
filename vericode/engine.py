"""Single entry point used by the CLI, the MCP server and the benchmark."""

from __future__ import annotations

import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from vericode.diff import parse_unified_diff
from vericode.extract import (
    DeclaredDep,
    ImportRef,
    discover,
    extract_imports,
    is_dependency_file,
    parse_pyproject,
    parse_requirements,
    third_party,
)
from vericode.registry import (
    Cache,
    LookupResult,
    NotFound,
    PackageInfo,
    Registry,
    Unknown,
    normalize,
)
from vericode.verdict import Check, Finding, Report, Severity


@dataclass
class _Collected:
    files: int = 0
    imports: list[ImportRef] = field(default_factory=list)
    deps: list[DeclaredDep] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


async def run_check(
    root: Path,
    paths: Sequence[Path] | None = None,
    diff: str | None = None,
    *,
    registry: Registry | None = None,
    use_cache: bool = True,
) -> Report:
    """Check files (or the files touched by a unified diff) under `root`.

    In diff mode only findings on added lines are reported, plus could-not-verify notices.
    """
    started = time.perf_counter()
    root = root.resolve()

    added: dict[str, set[int]] | None = None
    if diff is not None:
        added = parse_unified_diff(diff)
        files = [root / p for p in added if _checkable(root / p)]
    else:
        files = list(discover([p if p.is_absolute() else root / p for p in (paths or [root])]))

    collected = _collect(files, root)

    names = {normalize(r.top_level) for r in collected.imports}
    names |= {normalize(d.name) for d in collected.deps}

    reg = registry or Registry(Cache.default() if use_cache else Cache.disabled())
    async with reg:
        results = await reg.lookup_many(names)

    findings = collected.findings
    findings += _import_findings(collected.imports, results)
    findings += _dep_findings(collected.deps, results)
    if added is not None:
        findings = [f for f in findings if _on_added_line(f, added)]

    return Report(
        findings=findings,
        files_checked=collected.files,
        packages_checked=len(names),
        duration_s=time.perf_counter() - started,
    )


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


def _unparsable(rel: str, line: int | None, reason: str) -> Finding:
    return Finding(
        check=Check.COULD_NOT_VERIFY,
        severity=Severity.WARN,
        file=rel,
        line=line,
        reason=reason,
        fix_hint="Fix the file so it parses, then re-run the check.",
    )


def _import_findings(
    imports: Sequence[ImportRef], results: dict[str, LookupResult]
) -> list[Finding]:
    findings: list[Finding] = []
    unknown_reported: set[str] = set()
    for ref in imports:
        key = normalize(ref.top_level)
        res = results.get(key)
        if isinstance(res, NotFound):
            findings.append(
                Finding(
                    check=Check.PACKAGE_EXISTS,
                    severity=Severity.WARN if ref.optional else Severity.BLOCK,
                    file=ref.file,
                    line=ref.line,
                    package=key,
                    reason="Not found on PyPI (HTTP 404)"
                    + (
                        " - optional import inside try/except or TYPE_CHECKING"
                        if ref.optional
                        else ""
                    ),
                    fix_hint=f"Remove `import {ref.module}`: no package named '{key}' exists on "
                    "PyPI. Use a real package or write the code without it.",
                )
            )
        elif isinstance(res, Unknown) and key not in unknown_reported:
            unknown_reported.add(key)
            findings.append(_could_not_verify(ref.file, ref.line, key, res))
    return findings


def _dep_findings(deps: Sequence[DeclaredDep], results: dict[str, LookupResult]) -> list[Finding]:
    findings: list[Finding] = []
    for dep in deps:
        key = normalize(dep.name)
        res = results.get(key)
        if isinstance(res, NotFound):
            findings.append(
                Finding(
                    check=Check.PACKAGE_EXISTS,
                    severity=Severity.BLOCK,
                    file=dep.file,
                    line=dep.line,
                    package=key,
                    reason="Not found on PyPI (HTTP 404)",
                    fix_hint=f"Remove '{dep.name}' from {dep.file}: no package with this name "
                    "exists on PyPI.",
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


def _on_added_line(f: Finding, added: dict[str, set[int]]) -> bool:
    if f.check is Check.COULD_NOT_VERIFY or f.file is None or f.file not in added:
        return True
    return f.line is None or f.line in added[f.file]
