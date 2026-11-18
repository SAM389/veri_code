"""Risk signals and near-miss (typosquat) detection for packages that exist.

Everything here is computed from PyPI / pypistats facts handed in by the engine. Wording of
the resulting findings lives in `engine.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib import resources

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

from vericode.registry import PackageInfo, normalize

NEW_PACKAGE_DAYS = 30
LOW_DOWNLOADS = 1_000  # downloads in the last month
NEAR_MISS_MAX_DISTANCE = 2
NEAR_MISS_MIN_LENGTH = 5


@dataclass(frozen=True)
class TopPackages:
    ordered: tuple[str, ...]  # normalized, most popular first
    names: frozenset[str]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and normalize(name) in self.names


@cache
def top_packages() -> TopPackages:
    """The bundled snapshot of the most downloaded PyPI projects (`data/top_packages.txt`)."""
    text = resources.files("vericode").joinpath("data/top_packages.txt").read_text("utf-8")
    ordered = tuple(
        normalize(s) for line in text.splitlines() if (s := line.strip()) and not s.startswith("#")
    )
    return TopPackages(ordered, frozenset(ordered))


# --------------------------------------------------------------------------- near miss


@dataclass(frozen=True)
class NearMiss:
    candidate: str  # the popular package the name is close to
    distance: int  # Levenshtein edits


def near_miss(name: str, top: TopPackages, *, allow_variants: bool = False) -> NearMiss | None:
    """Closest popular package within 2 edits of `name`, or None.

    Never fires for names in the top list (both popular, e.g. httpx/httpie) or names shorter
    than 5 characters. Unless `allow_variants`, also skips candidates that differ only by
    separators or a `py`/`python-` prefix: those are usually the same project, not a typo.
    `allow_variants=True` is for names that do not exist, where the hint is all we add.
    """
    n = normalize(name)
    if len(n) < NEAR_MISS_MIN_LENGTH or n in top.names:
        return None
    matches = process.extract(
        n,
        top.ordered,
        scorer=Levenshtein.distance,
        score_cutoff=NEAR_MISS_MAX_DISTANCE,
        limit=10,
    )
    # closest first, then most popular (lower index in the ordered list)
    for cand, dist, _idx in sorted(matches, key=lambda m: (m[1], m[2])):
        if dist == 0 or (not allow_variants and _is_variant(n, cand)):
            continue
        return NearMiss(cand, int(dist))
    return None


_PREFIX = re.compile(r"^(python-|py-|py)")


def _is_variant(a: str, b: str) -> bool:
    def core(s: str) -> str:
        return _PREFIX.sub("", s).replace("-", "")

    return a.replace("-", "") == b.replace("-", "") or core(a) == core(b)


# --------------------------------------------------------------------------- risk signals


@dataclass(frozen=True)
class RiskSignals:
    first_release: datetime | None
    age_days: int | None
    downloads_last_month: int | None  # None when pypistats was unavailable
    release_count: int
    has_links: bool

    @property
    def new(self) -> bool:
        return self.age_days is not None and self.age_days < NEW_PACKAGE_DAYS

    @property
    def low_downloads(self) -> bool:
        return self.downloads_last_month is not None and self.downloads_last_month < LOW_DOWNLOADS

    @property
    def single_release(self) -> bool:
        return self.release_count <= 1

    @property
    def flagged(self) -> bool:
        """A strong signal (new, little-used) or both weak ones (one release, no links).

        One weak signal alone describes plenty of legitimate small packages.
        """
        return self.new or self.low_downloads or (self.single_release and not self.has_links)


def assess(info: PackageInfo, downloads_last_month: int | None, now: datetime) -> RiskSignals:
    times = [t for r in info.releases.values() if (t := _parse_time(r.upload_time))]
    first = min(times) if times else None
    with_files = sum(1 for r in info.releases.values() if r.upload_time)
    return RiskSignals(
        first_release=first,
        age_days=(now - first).days if first else None,
        downloads_last_month=downloads_last_month,
        release_count=with_files or len(info.releases),
        has_links=bool(info.home_page or any(info.project_urls.values())),
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:  # PyPI's legacy `upload_time` field is UTC without an offset
        dt = dt.replace(tzinfo=UTC)
    return dt
