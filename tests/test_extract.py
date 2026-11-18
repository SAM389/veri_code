from pathlib import Path
from textwrap import dedent

import pytest

from vericode.extract import (
    DeclaredDep,
    discover,
    extract_imports,
    parse_pyproject,
    parse_requirements,
    third_party,
)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(text), encoding="utf-8")
    return p


def _imports(tmp_path: Path, code: str) -> list[tuple[str, int, bool]]:
    p = _write(tmp_path, "m.py", code)
    return [(r.module, r.line, r.optional) for r in extract_imports(p, "m.py")]


# --------------------------------------------------------------------------- imports


def test_import_forms(tmp_path: Path) -> None:
    got = _imports(
        tmp_path,
        """\
        import requests
        import numpy as np, pandas
        from google.cloud import storage
        from fastapi.responses import JSONResponse
        """,
    )
    assert got == [
        ("requests", 1, False),
        ("numpy", 2, False),
        ("pandas", 2, False),
        ("google.cloud", 3, False),
        ("fastapi.responses", 4, False),
    ]


def test_relative_imports_skipped(tmp_path: Path) -> None:
    assert _imports(tmp_path, "from . import x\nfrom .utils import y\nfrom ..a import b\n") == []


def test_imports_inside_functions_found(tmp_path: Path) -> None:
    got = _imports(tmp_path, "def f():\n    import yaml\n")
    assert got == [("yaml", 2, False)]


@pytest.mark.parametrize(
    "handler",
    ["ImportError", "ModuleNotFoundError", "(ImportError, AttributeError)", "Exception", ""],
)
def test_try_except_import_error_is_optional(tmp_path: Path, handler: str) -> None:
    code = f"try:\n    import ujson\nexcept {handler}:\n    import json as ujson\n"
    assert _imports(tmp_path, code) == [("ujson", 2, True), ("json", 4, False)]


def test_try_except_other_error_not_optional(tmp_path: Path) -> None:
    code = "try:\n    import ujson\nexcept ValueError:\n    pass\n"
    assert _imports(tmp_path, code) == [("ujson", 2, False)]


def test_type_checking_imports_optional(tmp_path: Path) -> None:
    code = """\
    import typing
    if typing.TYPE_CHECKING:
        import boto3
    else:
        import botocore
    """
    assert _imports(tmp_path, code) == [
        ("typing", 1, False),
        ("boto3", 3, True),
        ("botocore", 5, False),
    ]


def test_syntax_error_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "bad.py", "import (\n")
    with pytest.raises(SyntaxError):
        extract_imports(p, "bad.py")


def test_third_party_drops_stdlib(tmp_path: Path) -> None:
    p = _write(
        tmp_path, "m.py", "import os, sys, json\nfrom __future__ import annotations\nimport x\n"
    )
    assert [r.module for r in third_party(extract_imports(p, "m.py"))] == ["x"]


# --------------------------------------------------------------------------- requirements.txt


def test_parse_requirements(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "requirements.txt",
        """\
        # a comment
        requests==2.31.0  # pinned
        fastapi[all]>=0.100
        -r other.txt
        -e .
        --index-url https://example.com/simple
        git+https://github.com/x/y.git
        ./local/pkg
        mypkg @ https://example.com/mypkg.whl
        uvicorn ; python_version >= "3.8"
        numpy \\
            >=1.26
        not a valid requirement!!
        """,
    )
    deps = parse_requirements(p, "requirements.txt")
    assert [(d.name, d.specifier, d.line) for d in deps] == [
        ("requests", "==2.31.0", 2),
        ("fastapi", ">=0.100", 3),
        ("uvicorn", "", 10),
        ("numpy", ">=1.26", 11),
    ]


@pytest.mark.parametrize(
    ("spec", "pinned"),
    [("==2.31.0", "2.31.0"), ("===1.0", "1.0"), (">=2", None), ("==2.*", None), ("", None)],
)
def test_pinned_version(spec: str, pinned: str | None) -> None:
    assert DeclaredDep("requests", spec, "r.txt", 1).pinned_version == pinned


# --------------------------------------------------------------------------- pyproject.toml


def test_parse_pyproject(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "pyproject.toml",
        """\
        [project]
        name = "demo"
        dependencies = [
            "httpx>=0.28",
            "requests==2.31.0",
        ]

        [project.optional-dependencies]
        cli = ["typer"]

        [dependency-groups]
        dev = ["pytest>=8", {include-group = "lint"}]
        lint = ["ruff"]

        [tool.poetry.dependencies]
        python = "^3.11"
        pendulum = "3.0.0"
        rich = "^13.0"
        localpkg = { path = "../localpkg" }
        """,
    )
    deps = {d.name: d for d in parse_pyproject(p, "pyproject.toml")}
    assert set(deps) == {"httpx", "requests", "typer", "pytest", "ruff", "pendulum", "rich"}
    assert deps["requests"].pinned_version == "2.31.0"
    assert deps["requests"].line == 5
    assert deps["pendulum"].pinned_version == "3.0.0"
    assert deps["rich"].pinned_version is None


# --------------------------------------------------------------------------- discovery


def test_discover_skips_venvs_and_hidden(tmp_path: Path) -> None:
    for rel in [
        "app/main.py",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "README.md",
        ".venv/lib/x.py",
        "myenv/pyvenv.cfg",
        "myenv/lib/y.py",
        ".hidden/z.py",
        "node_modules/a.py",
    ]:
        _write(tmp_path, rel, "")
    found = sorted(p.relative_to(tmp_path).as_posix() for p in discover([tmp_path]))
    assert found == ["app/main.py", "pyproject.toml", "requirements-dev.txt", "requirements.txt"]


def test_from_import_names_and_paths(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "m.py",
        """\
        import google.cloud.storage
        from google.cloud import storage, bigquery as bq
        from os import *
        """,
    )
    a, b, c = extract_imports(p, "m.py")
    assert (a.names, a.paths) == ((), ("google.cloud.storage",))
    assert b.names == ("storage", "bigquery")
    assert b.paths == ("google.cloud.storage", "google.cloud.bigquery")
    assert (c.names, c.paths) == ((), ("os",))
