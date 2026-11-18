"""Live checks against the real PyPI. Run with: pytest -m slow"""

from pathlib import Path

import pytest

from vericode.engine import run_check
from vericode.mapping import import_map
from vericode.registry import Cache, PackageInfo, Registry
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


async def test_live_import_map_targets_exist() -> None:
    """Every package the bundled map points at must exist, or mapping would cause BLOCKs."""
    targets = set(import_map().values())
    async with Registry(Cache.disabled()) as reg:
        results = await reg.lookup_many(targets)
    missing = sorted(n for n, r in results.items() if not isinstance(r, PackageInfo))
    assert missing == []


async def test_live_mapped_imports_pass(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text(
        "import cv2\nimport yaml\nfrom PIL import Image\nfrom sklearn import svm\n"
        "from google.cloud import storage\nfrom bs4 import BeautifulSoup\n",
        encoding="utf-8",
    )
    report = await run_check(tmp_path, use_cache=False)
    assert report.verdict is Severity.PASS, report.to_json()


async def test_live_typo_import_gets_hint(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("import reqeusts\n", encoding="utf-8")
    report = await run_check(tmp_path, use_cache=False)
    assert report.verdict is not Severity.PASS
    assert any("requests" in f.fix_hint for f in report.findings), report.to_json()
