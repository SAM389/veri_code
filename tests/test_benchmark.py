"""benchmark/run.py: case loading and scoring (offline; the real run talks to PyPI)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("_bench_run", ROOT / "benchmark" / "run.py")
assert spec and spec.loader
bench = importlib.util.module_from_spec(spec)
sys.modules["_bench_run"] = bench
spec.loader.exec_module(bench)


def test_all_cases_load_and_are_well_formed() -> None:
    cases = bench.load_cases()
    assert len(cases) >= 20
    for c in cases:
        assert c.kind in {"planted", "clean"}, c.id
        assert c.root.is_dir() and any(c.root.iterdir()), c.id
        if c.kind == "planted":
            assert c.expected_verdict == "BLOCK" and c.expected_findings, c.id
        else:
            assert c.expected_verdict == "PASS" and not c.expected_findings, c.id
    assert {c.kind for c in cases} == {"planted", "clean"}


def _case(kind: str, expected=(("package_exists", "fake-pkg"),)) -> object:
    return bench.Case(
        id="x",
        kind=kind,
        category="fake_package",
        expected_verdict="BLOCK" if kind == "planted" else "PASS",
        expected_findings=expected if kind == "planted" else (),
        note="",
        root=Path("."),
    )


def test_planted_needs_the_expected_finding() -> None:
    hit = bench.Result(
        _case("planted"), "BLOCK", [{"check": "package_exists", "package": "Fake_Pkg"}]
    )
    wrong = bench.Result(_case("planted"), "BLOCK", [{"check": "risk", "package": "fake-pkg"}])
    assert hit.ok
    assert not wrong.ok
    assert wrong.missing == [("package_exists", "fake-pkg")]


def test_clean_case_with_any_finding_is_a_false_positive() -> None:
    assert bench.Result(_case("clean"), "PASS", []).ok
    assert not bench.Result(_case("clean"), "WARN", [{"check": "risk", "package": "x"}]).ok


def test_summarize_counts_and_latency() -> None:
    results = [
        bench.Result(
            _case("planted"),
            "BLOCK",
            [{"check": "package_exists", "package": "fake-pkg"}],
            0.4,
            0.01,
        ),
        bench.Result(_case("planted"), "PASS", [], 0.5, 0.02),
        bench.Result(_case("clean"), "PASS", [], 0.3, 0.01),
        bench.Result(
            _case("clean"), "BLOCK", [{"check": "package_exists", "package": "y"}], 0.2, 0.01
        ),
    ]
    s = bench.summarize(results)
    assert (s["caught"], s["planted"]) == (1, 2)
    assert (s["false_positives"], s["false_blocks"], s["clean"]) == (1, 1, 2)
    assert s["by_category"]["fake_package"] == {"caught": 1, "total": 2}
    assert s["latency_s"]["cold_p50"] == 0.35
