"""Shared fixtures. Every test runs offline: PyPI is mocked with respx."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

FIXTURES = Path(__file__).parent / "fixtures"
PYPI_ROUTE = r"^https://pypi\.org/pypi/(?P<name>[^/]+)/json$"


def make_payload(
    name: str,
    versions: tuple[str, ...] = ("1.0.0",),
    *,
    yanked: tuple[str, ...] = (),
    upload_time: str = "2020-01-01T00:00:00.000000Z",
) -> dict[str, Any]:
    return {
        "info": {
            "name": name,
            "version": versions[-1],
            "home_page": "",
            "project_urls": {"Source": f"https://github.com/example/{name}"},
        },
        "releases": {
            v: [{"yanked": v in yanked, "upload_time_iso_8601": upload_time}] for v in versions
        },
    }


# Packages that "exist" on the mocked PyPI. Anything else returns 404.
DEFAULT_PACKAGES = {
    "requests": make_payload("requests", ("2.30.0", "2.31.0", "2.32.3"), yanked=("2.30.0",)),
    "fastapi": make_payload("fastapi", ("0.115.0",)),
    "httpx": make_payload("httpx", ("0.28.1",)),
}


class FakePyPI:
    def __init__(self, packages: dict[str, dict[str, Any]]) -> None:
        self.packages = dict(packages)
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request, name: str) -> httpx.Response:
        self.calls.append(name)
        if name in self.packages:
            return httpx.Response(200, json=self.packages[name])
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read or write the developer's real cache."""
    monkeypatch.setenv("VERICODE_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture
def pypi() -> Iterator[FakePyPI]:
    fake = FakePyPI(DEFAULT_PACKAGES)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=PYPI_ROUTE).mock(side_effect=fake)
        yield fake


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
