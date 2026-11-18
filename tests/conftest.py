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
PYPISTATS_ROUTE = r"^https://pypistats\.org/api/packages/(?P<name>[^/]+)/recent$"


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
    """Mocked PyPI JSON API plus pypistats. `downloads` maps name -> last-month downloads;
    names missing from it get a 404 from pypistats (the signal is then skipped)."""

    def __init__(self, packages: dict[str, dict[str, Any]]) -> None:
        self.packages = dict(packages)
        self.downloads: dict[str, int] = {}
        self.calls: list[str] = []
        self.stats_calls: list[str] = []

    def stats(self, request: httpx.Request, name: str) -> httpx.Response:
        self.stats_calls.append(name)
        if name in self.downloads:
            data = {"last_day": 0, "last_week": 0, "last_month": self.downloads[name]}
            return httpx.Response(200, json={"data": data, "package": name})
        return httpx.Response(404, json={"message": "Not Found"})

    def __call__(self, request: httpx.Request, name: str) -> httpx.Response:
        self.calls.append(name)
        if name in self.packages:
            return httpx.Response(200, json=self.packages[name])
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never read or write the developer's real cache."""
    monkeypatch.setenv("VERICODE_CACHE_DIR", str(tmp_path / "cache"))


@pytest.fixture(autouse=True)
def _no_installed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import mapping must not depend on what happens to be installed in the test env."""
    monkeypatch.setattr("vericode.mapping.installed_distributions", dict)


@pytest.fixture
def pypi() -> Iterator[FakePyPI]:
    fake = FakePyPI(DEFAULT_PACKAGES)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=PYPI_ROUTE).mock(side_effect=fake)
        router.get(url__regex=PYPISTATS_ROUTE).mock(side_effect=fake.stats)
        yield fake


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES
