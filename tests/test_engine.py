from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent

import httpx
import pytest
import respx

from tests.conftest import PYPI_ROUTE, PYPISTATS_ROUTE, FakePyPI, make_payload
from vericode.config import ConfigError
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


# --------------------------------------------------------------------------- phase 2


def _recent(days_ago: int) -> str:
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def test_p2_exit_gate_mapped_imports_pass_without_local_lookups(
    pypi: FakePyPI, tmp_path: Path
) -> None:
    for name in ("opencv-python", "pyyaml", "pillow", "scikit-learn", "google-cloud-storage"):
        pypi.packages[name] = make_payload(name, ("1.0.0", "2.0.0"))
    root = _project(
        tmp_path,
        {
            "app/main.py": """\
                import os
                import cv2
                import yaml
                from PIL import Image
                from sklearn.linear_model import LogisticRegression
                from google.cloud import storage
                from utils import helper
                import settings
                """,
            "utils/__init__.py": "def helper(): ...\n",
            "app/settings.py": "",
        },
    )
    report = await run_check(root)
    assert report.verdict is Severity.PASS, report.to_json()
    assert sorted(pypi.calls) == [
        "google-cloud-storage",
        "opencv-python",
        "pillow",
        "pyyaml",
        "scikit-learn",
    ]
    assert pypi.stats_calls == []  # all popular: no risk lookups


async def test_typo_import_blocks_with_did_you_mean(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"m.py": "import reqeusts\n"})
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity, f.package) == (Check.PACKAGE_EXISTS, Severity.BLOCK, "reqeusts")
    assert "Did you mean 'requests'?" in f.fix_hint


async def test_typo_dependency_blocks_with_did_you_mean(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"requirements.txt": "fastapii==0.1\n"})
    [f] = (await run_check(root)).findings
    assert f.severity is Severity.BLOCK
    assert f.fix_hint.startswith("Did you mean 'fastapi'?")


async def test_existing_typosquat_with_risk_warns_near_miss(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.packages["reqeusts"] = make_payload("reqeusts", ("0.0.1",), upload_time=_recent(3))
    root = _project(tmp_path, {"m.py": "import reqeusts\n"})
    report = await run_check(root)
    [f] = report.findings
    assert (f.check, f.severity, f.file, f.line) == (Check.NEAR_MISS, Severity.WARN, "m.py", 1)
    assert "'requests'" in f.reason and "days ago" in f.reason
    assert "Did you mean 'requests'?" in f.fix_hint


async def test_healthy_near_miss_is_not_reported(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.packages["httpie"] = make_payload("httpie", ("3.2.0", "3.2.4"))
    pypi.downloads["httpie"] = 900_000
    root = _project(tmp_path, {"m.py": "import httpie\nimport httpx\n"})
    report = await run_check(root)
    assert report.findings == []
    assert pypi.stats_calls == ["httpie"]  # httpx is in the top list: no stats lookup


async def test_new_package_warns_risk(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.packages["shiny-new-sdk"] = make_payload(
        "shiny-new-sdk", ("0.1.0", "0.2.0"), upload_time=_recent(4)
    )
    root = _project(tmp_path, {"a.py": "import os\nimport shiny_new_sdk\n"})
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity, f.package, f.line) == (
        Check.RISK,
        Severity.WARN,
        "shiny-new-sdk",
        2,
    )
    assert "first release" in f.reason


async def test_low_downloads_warns_risk(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.packages["quiet-lib"] = make_payload("quiet-lib", ("1.0", "1.1"))
    pypi.downloads["quiet-lib"] = 37
    root = _project(tmp_path, {"a.py": "import quiet_lib\n"})
    [f] = (await run_check(root)).findings
    assert f.check is Check.RISK
    assert "37 downloads last month" in f.reason


async def test_pypistats_down_skips_signal_silently(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.packages["quiet-lib"] = make_payload("quiet-lib", ("1.0", "1.1"))
    root = _project(tmp_path, {"a.py": "import quiet_lib\n"})
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=PYPI_ROUTE).mock(side_effect=pypi)
        router.get(url__regex=PYPISTATS_ROUTE).mock(side_effect=httpx.ConnectError("down"))
        report = await run_check(root)
    assert report.findings == []


async def test_undeclared_import_warns(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            "requirements.txt": "fastapi\n",
            "m.py": """\
                import fastapi
                import requests
                try:
                    import httpx
                except ImportError:
                    httpx = None
                """,
        },
    )
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity, f.package, f.line) == (
        Check.UNDECLARED,
        Severity.WARN,
        "requests",
        2,
    )
    assert "requirements.txt" in f.reason


async def test_no_dependency_files_means_no_undeclared_check(
    pypi: FakePyPI, tmp_path: Path
) -> None:
    root = _project(tmp_path, {"script.py": "import requests\n"})
    assert (await run_check(root)).findings == []


async def test_diff_mode_reads_root_requirements_for_undeclared(
    pypi: FakePyPI, tmp_path: Path
) -> None:
    root = _project(
        tmp_path, {"requirements.txt": "requests\n", "m.py": "import requests\nimport httpx\n"}
    )
    diff = """\
--- a/m.py
+++ b/m.py
@@ -1 +1,2 @@
 import requests
+import httpx
"""
    [f] = (await run_check(root, diff=diff)).findings
    assert (f.check, f.package, f.line) == (Check.UNDECLARED, "httpx", 2)


async def test_unknown_namespace_subpackage_only_warns(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {"m.py": "from azure.mgmt.madeupservice import Client\n"})
    [f] = (await run_check(root)).findings
    assert (f.check, f.severity, f.package) == (
        Check.PACKAGE_EXISTS,
        Severity.WARN,
        "azure-mgmt-madeupservice",
    )
    assert "guessed" in f.reason


async def test_config_allowlist_and_ignore_paths(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        {
            ".vericode/config.yaml": "allow: [acme-private]\nignore_paths: [legacy/]\n",
            "requirements.txt": "acme-private==1.0\nrequests\n",
            "m.py": "import acme_private\nimport requests\n",
            "legacy/old.py": "import some_dead_fake_pkg\n",
        },
    )
    report = await run_check(root)
    assert report.findings == []
    assert pypi.calls == ["requests"]  # private and ignored names never leave the machine


async def test_invalid_config_raises(pypi: FakePyPI, tmp_path: Path) -> None:
    root = _project(tmp_path, {".vericode/config.yaml": "allow: acme\n", "m.py": ""})
    with pytest.raises(ConfigError):
        await run_check(root)
    assert pypi.calls == []
