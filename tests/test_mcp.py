"""MCP server: tools go through engine.run_check, over a real in-process MCP client session."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tests.conftest import FakePyPI
from vericode.mcp_server import MAX_FILES, build_server, main


async def _call(name: str, args: dict[str, Any], *, local: bool = True) -> Any:
    async with Client(build_server(local=local)) as client:
        return await client.call_tool(name, args)


async def _tool_names(*, local: bool) -> set[str]:
    async with Client(build_server(local=local)) as client:
        return {t.name for t in (await client.list_tools()).tools}


async def test_tools_listed() -> None:
    assert await _tool_names(local=True) == {"verify_change", "verify_files"}
    # A remote server must never read its own filesystem.
    assert await _tool_names(local=False) == {"verify_files"}


async def test_verify_change_blocks_fake_import(pypi: FakePyPI, fixtures: Path) -> None:
    result = await _call("verify_change", {"root": str(fixtures / "fake_import")})
    assert not result.is_error
    data = result.structured_content
    assert data["verdict"] == "BLOCK"
    [f] = data["findings"]
    assert (f["check"], f["file"], f["package"]) == (
        "package_exists",
        "app/routes.py",
        "fastapi-auth-helpers",
    )
    assert f["fix_hint"]


async def test_verify_change_clean_passes(pypi: FakePyPI, fixtures: Path) -> None:
    result = await _call("verify_change", {"root": str(fixtures / "clean_requests")})
    assert result.structured_content["verdict"] == "PASS"


async def test_verify_change_diff_only_added_lines(pypi: FakePyPI, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("import fastapi_auth_helpers\nimport requests\n")
    (root / "requirements.txt").write_text("requests==2.31.0\n")
    diff = (
        "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,2 @@\n"
        " import fastapi_auth_helpers\n+import requests\n"
    )
    result = await _call("verify_change", {"root": str(root), "diff": diff})
    assert result.structured_content["verdict"] == "PASS"


async def test_verify_change_bad_root_is_error(tmp_path: Path) -> None:
    result = await _call("verify_change", {"root": str(tmp_path / "missing")})
    assert result.is_error
    assert "not a directory" in result.content[0].text


async def test_verify_files_blocks_fake_import(pypi: FakePyPI) -> None:
    files = {
        "app/routes.py": "import fastapi_auth_helpers\nimport requests\n",
        "requirements.txt": "requests==2.31.0\n",
    }
    result = await _call("verify_files", {"files": files}, local=False)
    data = result.structured_content
    assert data["verdict"] == "BLOCK"
    assert [f["package"] for f in data["findings"]] == ["fastapi-auth-helpers"]
    assert data["stats"]["files_checked"] == 2


async def test_verify_files_bad_pin_blocks(pypi: FakePyPI) -> None:
    files = {"main.py": "import requests\n", "requirements.txt": "requests==99.0.0\n"}
    data = (await _call("verify_files", {"files": files})).structured_content
    assert data["verdict"] == "BLOCK"
    assert data["findings"][0]["check"] == "version_exists"


async def test_verify_files_local_modules_not_looked_up(pypi: FakePyPI) -> None:
    files = {"main.py": "import helpers\n", "helpers.py": "X = 1\n"}
    data = (await _call("verify_files", {"files": files})).structured_content
    assert data["verdict"] == "PASS"
    assert pypi.calls == []


@pytest.mark.parametrize("bad", ["../evil.py", "/etc/x.py", "C:/x.py", "a/../../b.py", ""])
async def test_verify_files_rejects_escaping_paths(bad: str) -> None:
    result = await _call("verify_files", {"files": {bad: "x = 1\n"}})
    assert result.is_error
    assert "invalid file path" in result.content[0].text


async def test_verify_files_limits() -> None:
    result = await _call("verify_files", {"files": {}})
    assert result.is_error
    many = {f"m{i}.py": "" for i in range(MAX_FILES + 1)}
    result = await _call("verify_files", {"files": many})
    assert result.is_error and "too many files" in result.content[0].text


async def test_invalid_config_is_error_not_block(fixtures: Path, tmp_path: Path) -> None:
    root = tmp_path / "proj"
    shutil.copytree(fixtures / "clean_requests", root)
    (root / ".vericode").mkdir()
    (root / ".vericode" / "config.yaml").write_text("unknown_key: 1\n")
    result = await _call("verify_change", {"root": str(root)})
    assert result.is_error
    assert "invalid .vericode/config.yaml" in result.content[0].text


async def test_pypi_down_fails_open(fixtures: Path) -> None:
    import httpx
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"^https://pypi\.org/").mock(side_effect=httpx.ConnectError("down"))
        router.get(url__regex=r"^https://pypistats\.org/").mock(
            side_effect=httpx.ConnectError("down")
        )
        result = await _call("verify_change", {"root": str(fixtures / "fake_import")})
    data = result.structured_content
    assert data["verdict"] == "WARN"
    assert {f["check"] for f in data["findings"]} == {"could_not_verify"}


def test_main_rejects_unknown_transport() -> None:
    with pytest.raises(SystemExit):
        main(["--transport", "carrier-pigeon"])
