import json
from pathlib import Path

from typer.testing import CliRunner

from tests.conftest import FakePyPI
from vericode.cli import app

runner = CliRunner()


def test_fake_import_exits_1_with_block(pypi: FakePyPI, fixtures: Path) -> None:
    result = runner.invoke(app, ["check", "--root", str(fixtures / "fake_import")])
    assert result.exit_code == 1, result.output
    assert "BLOCK app/routes.py:1  package_exists  fastapi-auth-helpers" in result.output
    assert "Verdict: BLOCK" in result.output


def test_clean_project_exits_0(pypi: FakePyPI, fixtures: Path) -> None:
    result = runner.invoke(app, ["check", "--root", str(fixtures / "clean_requests")])
    assert result.exit_code == 0, result.output
    assert "Verdict: PASS" in result.output


def test_paths_relative_to_root(pypi: FakePyPI, fixtures: Path) -> None:
    result = runner.invoke(app, ["check", "app", "--root", str(fixtures / "fake_import")])
    assert result.exit_code == 1, result.output
    assert "1 files" in result.output  # only app/routes.py, not requirements.txt


def test_json_output(pypi: FakePyPI, fixtures: Path) -> None:
    result = runner.invoke(
        app, ["check", "--root", str(fixtures / "fake_import"), "--format", "json"]
    )
    data = json.loads(result.output)
    assert data["verdict"] == "BLOCK"
    assert data["findings"][0]["check"] == "package_exists"
    assert data["findings"][0]["package"] == "fastapi-auth-helpers"


def test_fail_on_warn(pypi: FakePyPI, tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("requests==2.30.0\n", encoding="utf-8")  # yanked
    base = ["check", "--root", str(tmp_path)]
    assert runner.invoke(app, base).exit_code == 0
    assert runner.invoke(app, [*base, "--fail-on", "warn"]).exit_code == 1


def test_diff_from_file_and_stdin(pypi: FakePyPI, tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("import requests\nimport fake_thing_zz\n", encoding="utf-8")
    patch = "--- a/m.py\n+++ b/m.py\n@@ -1 +1,2 @@\n import requests\n+import fake_thing_zz\n"
    (tmp_path / "change.patch").write_text(patch, encoding="utf-8")

    from_file = runner.invoke(
        app, ["check", "--root", str(tmp_path), "--diff", str(tmp_path / "change.patch")]
    )
    from_stdin = runner.invoke(app, ["check", "--root", str(tmp_path), "--diff", "-"], input=patch)
    for result in (from_file, from_stdin):
        assert result.exit_code == 1, result.output
        assert "m.py:2" in result.output


def test_internal_error_exits_3(pypi: FakePyPI, tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["check", "--root", str(tmp_path), "--diff", str(tmp_path / "missing.patch")]
    )
    assert result.exit_code == 3


def test_invalid_config_exits_3(pypi: FakePyPI, tmp_path: Path) -> None:
    (tmp_path / ".vericode").mkdir()
    (tmp_path / ".vericode" / "config.yaml").write_text("allow: not-a-list\n", encoding="utf-8")
    result = runner.invoke(app, ["check", "--root", str(tmp_path)])
    assert result.exit_code == 3
    assert "invalid config" in result.output
    assert pypi.calls == []
