from typer.testing import CliRunner

import vericode
from vericode.cli import app

runner = CliRunner()


def test_package_imports() -> None:
    assert isinstance(vericode.__version__, str)


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("vericode ")
