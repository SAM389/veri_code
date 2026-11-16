from vericode.verdict import Check, Finding, Report, Severity, sort_findings, verdict


def _f(sev: Severity, file: str = "a.py", line: int = 1) -> Finding:
    return Finding(check=Check.PACKAGE_EXISTS, severity=sev, file=file, line=line, package="x")


def test_verdict_empty_is_pass() -> None:
    assert verdict([]) is Severity.PASS


def test_verdict_is_worst_severity() -> None:
    assert verdict([_f(Severity.WARN)]) is Severity.WARN
    assert verdict([_f(Severity.WARN), _f(Severity.BLOCK), _f(Severity.PASS)]) is Severity.BLOCK


def test_sort_puts_block_first_then_location() -> None:
    fs = [
        _f(Severity.WARN, "a.py", 1),
        _f(Severity.BLOCK, "b.py", 9),
        _f(Severity.BLOCK, "a.py", 3),
    ]
    assert [(f.severity, f.file) for f in sort_findings(fs)] == [
        (Severity.BLOCK, "a.py"),
        (Severity.BLOCK, "b.py"),
        (Severity.WARN, "a.py"),
    ]


def test_finding_to_dict_drops_empty_fields() -> None:
    d = _f(Severity.BLOCK).to_dict()
    assert d == {
        "check": "package_exists",
        "severity": "BLOCK",
        "file": "a.py",
        "line": 1,
        "package": "x",
    }


def test_report_json_shape() -> None:
    r = Report(
        findings=[_f(Severity.BLOCK), _f(Severity.WARN)], files_checked=2, packages_checked=3
    )
    j = r.to_json()
    assert j["verdict"] == "BLOCK"
    assert j["stats"]["block"] == 1 and j["stats"]["warn"] == 1
    assert j["stats"]["files_checked"] == 2 and j["stats"]["packages_checked"] == 3
    assert [f["severity"] for f in j["findings"]] == ["BLOCK", "WARN"]
