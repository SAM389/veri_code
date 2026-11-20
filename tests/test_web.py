"""The browser build (web/): Pyodide entry point and the pure-Python rapidfuzz stand-in.

Modules are loaded from their file paths under private names, so the stand-in never shadows
the real rapidfuzz that the rest of the suite uses.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from rapidfuzz import process as real_process
from rapidfuzz.distance import Levenshtein as RealLevenshtein

from tests.conftest import DEFAULT_PACKAGES
from vericode.registry import Cache, normalize
from vericode.risk import top_packages

WEB = Path(__file__).resolve().parent.parent / "web"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


shim_distance = _load("_web_rf_distance", WEB / "py" / "rapidfuzz" / "distance.py")
shim_process = _load("_web_rf_process", WEB / "py" / "rapidfuzz" / "process.py")
web = _load("_web_vericode_web", WEB / "py" / "vericode_web.py")


# --------------------------------------------------------------------------- rapidfuzz shim

PAIRS = [
    ("requests", "reqeusts"),
    ("numpy", "numpyy"),
    ("", "abc"),
    ("kitten", "sitting"),
    ("httpx", "httpie"),
    ("a", "abcdef"),
    ("flask", "flask"),
]


@pytest.mark.parametrize(("a", "b"), PAIRS)
@pytest.mark.parametrize("cutoff", [None, 0, 1, 2])
def test_shim_distance_matches_rapidfuzz(a: str, b: str, cutoff: int | None) -> None:
    lev = shim_distance.Levenshtein
    assert lev.distance(a, b, score_cutoff=cutoff) == RealLevenshtein.distance(
        a, b, score_cutoff=cutoff
    )


@pytest.mark.parametrize("query", ["reqeusts", "numpyy", "pandsa", "fastapi-auth-helpers", "djang"])
def test_shim_extract_matches_rapidfuzz(query: str) -> None:
    choices = top_packages().ordered

    def run(proc: ModuleType, scorer: object) -> list[tuple[str, int]]:
        out = proc.extract(query, choices, scorer=scorer, score_cutoff=2, limit=10)
        return sorted((c, int(d)) for c, d, _i in out)

    assert run(shim_process, shim_distance.Levenshtein.distance) == run(
        real_process, RealLevenshtein.distance
    )


# --------------------------------------------------------------------------- entry point


def _fake_fetch(packages: dict[str, dict]):
    async def fetch(url: str) -> tuple[int, str]:
        name = normalize(url.split("/pypi/", 1)[1].split("/", 1)[0])
        if name in packages:
            return 200, json.dumps(packages[name])
        return 404, "{}"

    return fetch


@pytest.fixture(autouse=True)
def _no_web_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_CACHE", Cache.disabled())


async def _check(files: dict[str, str], fetch=None) -> dict:
    out = await web.check(json.dumps(files), fetch or _fake_fetch(DEFAULT_PACKAGES))
    return json.loads(out)


async def test_check_blocks_made_up_package_with_hint() -> None:
    r = await _check({"main.py": "import reqeusts\n", "requirements.txt": "requests\n"})
    assert r["verdict"] == "BLOCK"
    (f,) = r["findings"]
    assert (f["check"], f["file"], f["line"]) == ("package_exists", "main.py", 1)
    assert "requests" in f["fix_hint"]


async def test_check_blocks_missing_version() -> None:
    r = await _check({"main.py": "import requests\n", "requirements.txt": "requests==99.0.0\n"})
    assert r["verdict"] == "BLOCK"
    assert r["findings"][0]["check"] == "version_exists"


async def test_check_passes_clean_code() -> None:
    r = await _check({"main.py": "import os\nimport requests\n", "requirements.txt": "requests\n"})
    assert r["verdict"] == "PASS"
    assert r["findings"] == []


async def test_network_failure_is_warn_not_block() -> None:
    async def broken(url: str) -> tuple[int, str]:
        raise OSError("offline")

    r = await _check({"main.py": "import requests\n"}, broken)
    assert r["verdict"] == "WARN"
    assert {f["check"] for f in r["findings"]} == {"could_not_verify"}


async def test_fetch_client_skips_pypistats_without_a_request() -> None:
    calls: list[str] = []

    async def fetch(url: str) -> tuple[int, str]:
        calls.append(url)
        return 200, "{}"

    with pytest.raises(httpx.ConnectError):
        await web.FetchClient(fetch).get("https://pypistats.org/api/packages/x/recent")
    assert calls == []


@pytest.mark.parametrize("name", ["../evil.py", "/abs.py", "C:/x.py", ""])
async def test_check_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(ValueError):
        await _check({name: "import os\n"})


async def test_check_rejects_too_many_files() -> None:
    with pytest.raises(ValueError):
        await _check({f"m{i}.py": "" for i in range(web.MAX_FILES + 1)})


# --------------------------------------------------------------------------- build


def test_build_bundles_engine_without_cli_or_mcp(tmp_path: Path, monkeypatch) -> None:
    build = _load("_web_build", WEB / "build.py")
    monkeypatch.setattr(build, "OUT", tmp_path / "vericode.zip")
    names = set(zipfile.ZipFile(build.build()).namelist())
    assert {
        "vericode/engine.py",
        "vericode/data/top_packages.txt",
        "vericode/data/import_map.json",
        "vericode_web.py",
        "rapidfuzz/process.py",
        "rapidfuzz/distance.py",
    } <= names
    assert "vericode/cli.py" not in names
    assert "vericode/mcp_server.py" not in names
