from pathlib import Path
from textwrap import dedent

import httpx
import respx

from tests.conftest import PYPI_ROUTE, FakePyPI
from vericode.engine import run_check
from vericode.verdict import Check, Severity


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / "proj" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dedent(text), encoding="utf-8")
    return tmp_path / "proj"


async def test_fake_import_blocks(pypi: FakePyPI, fixtures: Path) -> None:
    report = await run_check(fixtures / "fake_import")
    assert report.verdict is Severity.BLOCK
    [f] = report.findings
    assert (f.check, f.file, f.line, f.package) == (
        Check.PACKAGE_EXISTS,
        "app/routes.py",
        1,
        "fastapi-auth-helpers",
    )
    assert f.reason.startswith("Not found on PyPI")
    assert f.fix_hint


async def test_clean_project_passes(pypi: FakePyPI, fixtures: Path) -> None:
    report = await run_check(fixtures / "clean_requests")
    assert report.verdict is Severity.PASS
    assert report.findings == []
    assert pypi.calls == ["requests"]  # stdlib (os, json) never queried


async def test_bad_pinned_version_blocks(pypi: FakePyPI, fixtures: Path) -> None:
    report = await run_check(fixtures / "bad_version")
    [f] = report.findings
    assert (f.check, f.severity, f.file, f.line) == (
        Check.VERSION_EXISTS,
        Severity.BLOCK,
        "requirements.txt",
        2,
    )
    assert "2.32.3" in f.fix_hint  # points at the real latest version


async def test_yanked_version_warns(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"requirements.txt": "requests==2.30.0\n"})
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity) == (Check.VERSION_EXISTS, Severity.WARN)


async def test_fake_declared_dependency_blocks(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(
        tmp_path, {"pyproject.toml": '[project]\ndependencies = ["fastapi", "reqests-pro"]\n'}
    )
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity, f.file, f.line) == (
        Check.PACKAGE_EXISTS,
        Severity.BLOCK,
        "pyproject.toml",
        2,
    )


async def test_optional_fake_import_only_warns(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(
        tmp_path, {"m.py": "try:\n    import turbo_json_x\nexcept ImportError:\n    pass\n"}
    )
    report = await run_check(root)
    assert report.verdict is Severity.WARN


async def test_pypi_down_fails_open(tmp_path: Path) -> None:
    root = _project(tmp_path, {"m.py": "import requests\nimport fakepkg_zz\n"})
    with respx.mock() as router:
        router.get(url__regex=PYPI_ROUTE).mock(side_effect=httpx.ConnectError("offline"))
        report = await run_check(root)
    assert report.verdict is Severity.WARN  # never BLOCK on our own failure
    assert {f.check for f in report.findings} == {Check.COULD_NOT_VERIFY}
    assert len(report.findings) == 2


async def test_syntax_error_warns_and_continues(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"bad.py": "import (\n", "ok.py": "import fakepkg_zz\n"})
    report = await run_check(root)
    checks = {(f.file, f.check) for f in report.findings}
    assert ("bad.py", Check.COULD_NOT_VERIFY) in checks
    assert ("ok.py", Check.PACKAGE_EXISTS) in checks


async def test_diff_mode_reports_only_added_lines(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"m.py": "import old_fake_pkg\nimport new_fake_pkg\n"})
    diff = """\
--- a/m.py
+++ b/m.py
@@ -1 +1,2 @@
 import old_fake_pkg
+import new_fake_pkg
"""
    report = await run_check(root, diff=diff)
    assert [(f.package, f.line) for f in report.findings] == [("new-fake-pkg", 2)]
