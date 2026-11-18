from pathlib import Path

import httpx
import pytest
import respx

from tests.conftest import PYPI_ROUTE, PYPISTATS_ROUTE, FakePyPI, make_payload
from vericode.registry import (
    FOUND_TTL_S,
    NOT_FOUND_TTL_S,
    Cache,
    NotFound,
    PackageInfo,
    Registry,
    Unknown,
    normalize,
)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def _registry(tmp_path: Path, clock: Clock | None = None) -> Registry:
    return Registry(Cache(tmp_path / "pypi.json", now=clock or Clock()))


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("Requests", "requests"),
        ("python_dotenv", "python-dotenv"),
        ("zope.interface", "zope-interface"),
    ],
)
def test_normalize(raw: str, norm: str) -> None:
    assert normalize(raw) == norm


async def test_found_and_not_found(pypi: FakePyPI, tmp_path: Path) -> None:
    async with _registry(tmp_path) as reg:
        res = await reg.lookup_many(["Requests", "fastapi_auth_helpers"])
    assert isinstance(res["requests"], PackageInfo)
    assert res["requests"].latest_version == "2.32.3"
    assert isinstance(res["fastapi-auth-helpers"], NotFound)


async def test_version_status(pypi: FakePyPI, tmp_path: Path) -> None:
    async with _registry(tmp_path) as reg:
        info = await reg.lookup("requests")
    assert isinstance(info, PackageInfo)
    assert info.version_status("2.31.0") == "present"
    assert info.version_status("2.31") == "present"  # PEP 440: 2.31 == 2.31.0
    assert info.version_status("2.31.1") == "missing"
    assert info.version_status("2.30.0") == "yanked"
    assert info.version_status("99.0.0") == "missing"
    assert info.version_status("not-a-version") == "missing"


async def test_version_status_normalizes_versions(tmp_path: Path) -> None:
    info = PackageInfo.from_pypi(make_payload("x", ("1.0",)))
    assert info.version_status("1.0.0") == "present"  # PEP 440: 1.0 == 1.0.0


async def test_timeout_is_unknown_after_retry(tmp_path: Path) -> None:
    with respx.mock() as router:
        route = router.get(url__regex=PYPI_ROUTE).mock(side_effect=httpx.ReadTimeout("slow"))
        async with _registry(tmp_path) as reg:
            res = await reg.lookup("requests")
    assert isinstance(res, Unknown)
    assert "timed out" in res.error
    assert route.call_count == 2  # one retry


async def test_5xx_then_success(tmp_path: Path) -> None:
    with respx.mock() as router:
        router.get(url__regex=PYPI_ROUTE).mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json=make_payload("requests"))]
        )
        async with _registry(tmp_path) as reg:
            res = await reg.lookup("requests")
    assert isinstance(res, PackageInfo)


async def test_429_is_unknown_without_retry(tmp_path: Path) -> None:
    with respx.mock() as router:
        route = router.get(url__regex=PYPI_ROUTE).mock(return_value=httpx.Response(429))
        async with _registry(tmp_path) as reg:
            res = await reg.lookup("requests")
    assert isinstance(res, Unknown)
    assert route.call_count == 1


async def test_cache_hit_avoids_network(pypi: FakePyPI, tmp_path: Path) -> None:
    clock = Clock()
    async with _registry(tmp_path, clock) as reg:
        await reg.lookup_many(["requests", "nope-not-real"])
    assert (tmp_path / "pypi.json").is_file()
    pypi.calls.clear()

    async with _registry(tmp_path, clock) as reg:  # fresh process, same cache file
        res = await reg.lookup_many(["requests", "nope-not-real"])
    assert pypi.calls == []
    assert isinstance(res["requests"], PackageInfo)
    assert res["requests"].version_status("2.30.0") == "yanked"  # survives the round trip
    assert isinstance(res["nope-not-real"], NotFound)


async def test_cache_ttls(pypi: FakePyPI, tmp_path: Path) -> None:
    clock = Clock()
    async with _registry(tmp_path, clock) as reg:
        await reg.lookup_many(["requests", "nope-not-real"])

    clock.t += NOT_FOUND_TTL_S + 1  # not-found expired, found still fresh
    pypi.calls.clear()
    async with _registry(tmp_path, clock) as reg:
        await reg.lookup_many(["requests", "nope-not-real"])
    assert pypi.calls == ["nope-not-real"]

    clock.t += FOUND_TTL_S + 1
    pypi.calls.clear()
    async with _registry(tmp_path, clock) as reg:
        await reg.lookup_many(["requests"])
    assert pypi.calls == ["requests"]


async def test_unknown_is_never_cached(tmp_path: Path) -> None:
    with respx.mock() as router:
        route = router.get(url__regex=PYPI_ROUTE).mock(return_value=httpx.Response(429))
        for _ in range(2):
            async with _registry(tmp_path) as reg:
                await reg.lookup("requests")
    assert route.call_count == 2


async def test_corrupt_cache_is_ignored(pypi: FakePyPI, tmp_path: Path) -> None:
    (tmp_path / "pypi.json").write_text("{not json", encoding="utf-8")
    async with _registry(tmp_path) as reg:
        assert isinstance(await reg.lookup("requests"), PackageInfo)


async def test_disabled_cache_writes_nothing(pypi: FakePyPI, tmp_path: Path) -> None:
    async with Registry(Cache.disabled()) as reg:
        await reg.lookup("requests")
    assert list(tmp_path.iterdir()) == []


async def test_invalid_name_not_queried(pypi: FakePyPI, tmp_path: Path) -> None:
    async with _registry(tmp_path) as reg:
        res = await reg.lookup("-bad-")
    assert isinstance(res, Unknown)
    assert pypi.calls == []


# --------------------------------------------------------------------------- pypistats


async def test_downloads_found_and_cached(pypi: FakePyPI, tmp_path: Path) -> None:
    pypi.downloads["requests"] = 123_456
    clock = Clock()
    async with _registry(tmp_path, clock) as reg:
        assert await reg.downloads_last_month("Requests") == 123_456
    async with _registry(tmp_path, clock) as reg:
        assert await reg.downloads_many(["requests"]) == {"requests": 123_456}
    assert pypi.stats_calls == ["requests"]
    clock.t += FOUND_TTL_S + 1
    async with _registry(tmp_path, clock) as reg:
        await reg.downloads_last_month("requests")
    assert pypi.stats_calls == ["requests", "requests"]


async def test_downloads_cache_does_not_shadow_package_entry(
    pypi: FakePyPI, tmp_path: Path
) -> None:
    pypi.downloads["requests"] = 5
    async with _registry(tmp_path) as reg:
        await reg.downloads_last_month("requests")
        assert isinstance(await reg.lookup("requests"), PackageInfo)


async def test_downloads_failures_are_none_and_not_cached(tmp_path: Path) -> None:
    with respx.mock() as router:
        route = router.get(url__regex=PYPISTATS_ROUTE)
        route.side_effect = [
            httpx.ConnectError("offline"),
            httpx.Response(429),
            httpx.Response(200, json={"unexpected": True}),
            httpx.Response(200, json={"data": {"last_month": 42}}),
        ]
        async with _registry(tmp_path) as reg:
            results = [await reg.downloads_last_month("pkg") for _ in range(4)]
    assert results == [None, None, None, 42]
    assert route.call_count == 4  # no retries, failures not cached
