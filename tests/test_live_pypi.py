"""Live checks against the real PyPI. Run with: pytest -m slow"""

from pathlib import Path

import pytest

from vericode.engine import run_check
from vericode.verdict import Severity

pytestmark = pytest.mark.slow


async def test_live_fake_import_blocks(fixtures: Path) -> None:
    report = await run_check(fixtures / "fake_import", use_cache=False)
    assert report.verdict is Severity.BLOCK
    assert {f.package for f in report.findings} == {"fastapi-auth-helpers"}


async def test_live_clean_project_passes(fixtures: Path) -> None:
    report = await run_check(fixtures / "clean_requests", use_cache=False)
    assert report.verdict is Severity.PASS, report.to_json()


async def test_live_bad_version_blocks(fixtures: Path) -> None:
    report = await run_check(fixtures / "bad_version", use_cache=False)
    assert report.verdict is Severity.BLOCK
