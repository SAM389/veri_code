from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import make_payload
from vericode.registry import PackageInfo
from vericode.risk import TopPackages, assess, near_miss, top_packages

NOW = datetime(2026, 11, 17, tzinfo=UTC)


def _iso(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _info(
    versions: tuple[str, ...] = ("1.0.0", "1.1.0"), *, days_ago: int = 400, links: bool = True
) -> PackageInfo:
    payload = make_payload("pkg", versions, upload_time=_iso(days_ago))
    if not links:
        payload["info"]["project_urls"] = None
    return PackageInfo.from_pypi(payload)


def test_top_packages_snapshot_is_loaded() -> None:
    top = top_packages()
    assert len(top.ordered) >= 4000
    assert "requests" in top
    assert "Typing_Extensions" in top  # normalized membership
    assert "fastapi-auth-helpers" not in top


@pytest.mark.parametrize(
    ("name", "candidate"),
    [("reqeusts", "requests"), ("numpyy", "numpy"), ("fastapii", "fastapi"), ("pandsa", "pandas")],
)
def test_near_miss_finds_popular_package(name: str, candidate: str) -> None:
    nm = near_miss(name, top_packages())
    assert nm is not None
    assert nm.candidate == candidate
    assert nm.distance <= 2


def test_near_miss_ignores_popular_and_short_names() -> None:
    top = top_packages()
    assert near_miss("requests", top) is None  # in the list itself
    assert near_miss("nmpy", top) is None  # shorter than 5
    both = TopPackages(("httpx", "httpie"), frozenset({"httpx", "httpie"}))
    assert near_miss("httpie", both) is None  # both popular


def test_near_miss_skips_separator_and_prefix_variants_unless_allowed() -> None:
    top = top_packages()
    assert near_miss("scikitlearn", top) is None
    nm = near_miss("scikitlearn", top, allow_variants=True)
    assert nm is not None and nm.candidate == "scikit-learn"


def test_nothing_close_returns_none() -> None:
    assert near_miss("fastapi-auth-helpers", top_packages()) is None


def test_established_package_is_not_flagged() -> None:
    s = assess(_info(), downloads_last_month=50_000, now=NOW)
    assert not s.flagged
    assert s.age_days == 400


def test_new_package_is_flagged() -> None:
    s = assess(_info(days_ago=5), downloads_last_month=None, now=NOW)
    assert s.new and s.flagged
    assert s.first_release is not None and s.first_release.date() == (NOW - timedelta(5)).date()


def test_low_downloads_is_flagged() -> None:
    s = assess(_info(), downloads_last_month=120, now=NOW)
    assert s.low_downloads and s.flagged


def test_missing_download_data_is_skipped() -> None:
    s = assess(_info(), downloads_last_month=None, now=NOW)
    assert not s.low_downloads and not s.flagged


def test_one_weak_signal_alone_is_not_flagged() -> None:
    assert not assess(_info(("1.0.0",)), None, NOW).flagged  # single release, has links
    assert not assess(_info(links=False), None, NOW).flagged  # no links, many releases


def test_both_weak_signals_flag() -> None:
    s = assess(_info(("1.0.0",), links=False), None, NOW)
    assert s.single_release and not s.has_links and s.flagged
