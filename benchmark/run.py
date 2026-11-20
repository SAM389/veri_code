"""Benchmark runner: catch rate, false positives and latency over benchmark/cases.

Each case is `benchmark/cases/<id>/case.yaml` + a mini project in `files/`:

    kind: planted | clean
    category: fake_package | typo_package | fake_version | ...
    expected_verdict: BLOCK | WARN | PASS
    expected_findings: [{check: package_exists, package: name}]   # planted cases

A planted case is caught when the verdict matches and every expected finding is reported.
A clean case is a false positive when it gets any WARN or BLOCK finding.
Every case runs twice: cold (empty PyPI cache) and warm (cache filled by the cold run).
This talks to the real PyPI, so run it online:

    python benchmark/run.py            # writes benchmark/results.json + benchmark/RESULTS.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from vericode import __version__
from vericode.engine import run_check
from vericode.registry import Cache, Registry, normalize
from vericode.verdict import Report, Severity

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"


@dataclass(frozen=True)
class Case:
    id: str
    kind: str  # planted | clean
    category: str
    expected_verdict: str
    expected_findings: tuple[tuple[str, str], ...]  # (check, normalized package)
    note: str
    root: Path


@dataclass
class Result:
    case: Case
    verdict: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    cold_s: float = 0.0
    warm_s: float = 0.0

    @property
    def missing(self) -> list[tuple[str, str]]:
        got = {(f["check"], normalize(f.get("package", ""))) for f in self.findings}
        return [e for e in self.case.expected_findings if e not in got]

    @property
    def ok(self) -> bool:
        if self.case.kind == "planted":
            return self.verdict == self.case.expected_verdict and not self.missing
        return not self.findings  # clean: any WARN/BLOCK is a false positive


def load_cases(root: Path = CASES) -> list[Case]:
    cases = []
    for d in sorted(p for p in root.iterdir() if (p / "case.yaml").is_file()):
        meta = yaml.safe_load((d / "case.yaml").read_text(encoding="utf-8"))
        cases.append(
            Case(
                id=d.name,
                kind=meta["kind"],
                category=meta["category"],
                expected_verdict=meta["expected_verdict"],
                expected_findings=tuple(
                    (f["check"], normalize(f["package"])) for f in meta.get("expected_findings", [])
                ),
                note=meta.get("note", ""),
                root=d / "files",
            )
        )
    return cases


async def _timed(case: Case, cache: Cache) -> tuple[Report, float]:
    started = time.perf_counter()
    report = await run_check(case.root, registry=Registry(cache))
    return report, time.perf_counter() - started


async def run_case(case: Case, cache_dir: Path) -> Result:
    cache_path = cache_dir / case.id / "pypi.json"
    cold, cold_s = await _timed(case, Cache(cache_path))  # empty cache
    warm, warm_s = await _timed(case, Cache(cache_path))  # filled by the cold run
    findings = [f.to_dict() for f in warm.findings if f.severity in (Severity.WARN, Severity.BLOCK)]
    if str(cold.verdict) != str(warm.verdict):
        raise RuntimeError(f"{case.id}: cold and warm verdicts differ")
    return Result(case, str(warm.verdict), findings, cold_s, warm_s)


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarize(results: list[Result]) -> dict[str, Any]:
    planted = [r for r in results if r.case.kind == "planted"]
    clean = [r for r in results if r.case.kind == "clean"]
    cold = [r.cold_s for r in results]
    warm = [r.warm_s for r in results]
    by_category: dict[str, list[int]] = {}
    for r in planted:
        hit = by_category.setdefault(r.case.category, [0, 0])
        hit[0] += r.ok
        hit[1] += 1
    return {
        "caught": sum(r.ok for r in planted),
        "planted": len(planted),
        "false_positives": sum(not r.ok for r in clean),
        "clean": len(clean),
        "false_blocks": sum(r.verdict == "BLOCK" for r in clean),
        "by_category": {k: {"caught": v[0], "total": v[1]} for k, v in by_category.items()},
        "latency_s": {
            "cold_p50": round(statistics.median(cold), 3),
            "cold_p95": round(_pct(cold, 0.95), 3),
            "warm_p50": round(statistics.median(warm), 3),
            "warm_p95": round(_pct(warm, 0.95), 3),
        },
    }


def _markdown(summary: dict[str, Any], results: list[Result], meta: dict[str, str]) -> str:
    lat = summary["latency_s"]
    lines = [
        "# VeriCode benchmark results",
        "",
        f"Run {meta['date']} · vericode {meta['version']} · Python {meta['python']} · "
        "live PyPI. Generated by `python benchmark/run.py`.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Planted issues caught | **{summary['caught']} / {summary['planted']}** |",
        f"| False positives on clean code (any WARN/BLOCK) | "
        f"**{summary['false_positives']} / {summary['clean']}** |",
        f"| False BLOCKs on clean code | **{summary['false_blocks']} / {summary['clean']}** |",
        f"| Latency, cold cache (p50 / p95) | {lat['cold_p50']} s / {lat['cold_p95']} s |",
        f"| Latency, warm cache (p50 / p95) | {lat['warm_p50']} s / {lat['warm_p95']} s |",
        "",
        "## Planted issues by category",
        "",
        "| Category | Caught |",
        "|---|---|",
    ]
    for cat, v in summary["by_category"].items():
        lines.append(f"| {cat} | {v['caught']} / {v['total']} |")
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Kind | Expected | Got | OK | Cold s | Warm s |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.case.id} | {r.case.kind} | {r.case.expected_verdict} | {r.verdict} | "
            f"{'yes' if r.ok else 'NO'} | {r.cold_s:.2f} | {r.warm_s:.2f} |"
        )
    misses = [r for r in results if not r.ok]
    if misses:
        lines += ["", "## Misses and false positives", ""]
        for r in misses:
            lines.append(f"- **{r.case.id}**: {r.case.note}")
            lines += [
                f"  - got `{f['check']}` {f.get('package', '')}: {f['reason']}" for f in r.findings
            ]
            lines += [f"  - missing `{c}` {p}" for c, p in r.missing]
    return "\n".join(lines) + "\n"


async def main_async(cases_dir: Path, out_dir: Path) -> dict[str, Any]:
    cases = load_cases(cases_dir)
    with tempfile.TemporaryDirectory(prefix="vericode-bench-") as tmp:
        results = [await run_case(c, Path(tmp)) for c in cases]
    summary = summarize(results)
    meta = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "version": __version__,
        "python": platform.python_version(),
    }
    payload = {
        **meta,
        "summary": summary,
        "cases": [
            {
                "id": r.case.id,
                "kind": r.case.kind,
                "category": r.case.category,
                "expected": r.case.expected_verdict,
                "verdict": r.verdict,
                "ok": r.ok,
                "cold_s": round(r.cold_s, 3),
                "warm_s": round(r.warm_s, 3),
                "findings": r.findings,
            }
            for r in results
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (out_dir / "RESULTS.md").write_text(_markdown(summary, results, meta), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cases", type=Path, default=CASES)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args(argv)
    s = asyncio.run(main_async(args.cases, args.out))
    lat = s["latency_s"]
    print(
        f"caught {s['caught']}/{s['planted']}, "
        f"false positives {s['false_positives']}/{s['clean']} "
        f"({s['false_blocks']} false BLOCKs), "
        f"latency p50 cold {lat['cold_p50']}s / warm {lat['warm_p50']}s"
    )


if __name__ == "__main__":
    main()
