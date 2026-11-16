"""Finding / Severity model and findings -> PASS/WARN/BLOCK verdict.

This schema is shared by the CLI, the MCP server and the benchmark, so keep it stable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {Severity.PASS: 0, Severity.WARN: 1, Severity.BLOCK: 2}


class Check(StrEnum):
    PACKAGE_EXISTS = "package_exists"
    VERSION_EXISTS = "version_exists"
    RISK = "risk"
    NEAR_MISS = "near_miss"
    UNDECLARED = "undeclared"
    SYMBOL_EXISTS = "symbol_exists"
    COULD_NOT_VERIFY = "could_not_verify"


@dataclass(frozen=True)
class Finding:
    check: Check
    severity: Severity
    file: str | None
    line: int | None
    package: str | None = None
    symbol: str | None = None
    reason: str = ""  # always comes from a real tool (PyPI, pyright, ast), never an LLM
    fix_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["check"] = str(self.check)
        d["severity"] = str(self.severity)
        return {k: v for k, v in d.items() if v not in (None, "")}


def verdict(findings: Iterable[Finding]) -> Severity:
    """Worst severity across findings; no findings means PASS."""
    return max((f.severity for f in findings), key=lambda s: s.rank, default=Severity.PASS)


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda f: (-f.severity.rank, f.file or "", f.line or 0, str(f.check), f.package or ""),
    )


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    files_checked: int = 0
    packages_checked: int = 0
    duration_s: float = 0.0

    @property
    def verdict(self) -> Severity:
        return verdict(self.findings)

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity is severity)

    def to_json(self) -> dict[str, Any]:
        return {
            "verdict": str(self.verdict),
            "findings": [f.to_dict() for f in sort_findings(self.findings)],
            "stats": {
                "block": self.count(Severity.BLOCK),
                "warn": self.count(Severity.WARN),
                "files_checked": self.files_checked,
                "packages_checked": self.packages_checked,
                "duration_s": round(self.duration_s, 3),
            },
        }
