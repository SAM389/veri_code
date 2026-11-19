"""The shipped Agent Skill follows the agentskills.io format and matches the real tools."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from mcp import Client

from vericode.mcp_server import build_server
from vericode.verdict import Check

SKILL_DIR = Path(__file__).parent.parent / ".agents" / "skills" / "vericode-verify"


def _frontmatter() -> tuple[dict[str, str], str]:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert m, "SKILL.md must start with YAML frontmatter between --- lines"
    return yaml.safe_load(m.group(1)), m.group(2)


def test_frontmatter_follows_spec() -> None:
    meta, body = _frontmatter()
    assert set(meta) == {"name", "description"}
    assert meta["name"] == SKILL_DIR.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", meta["name"]) and len(meta["name"]) <= 64
    assert 0 < len(meta["description"]) < 1024
    assert len(body.splitlines()) < 500


async def test_skill_names_real_mcp_tools() -> None:
    _, body = _frontmatter()
    async with Client(build_server(local=True)) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
    named = set(re.findall(r"`(verify_\w+)`", body))
    assert named == tools


def test_reference_covers_every_check() -> None:
    _, body = _frontmatter()
    assert "references/checks.md" in body
    ref = (SKILL_DIR / "references" / "checks.md").read_text(encoding="utf-8")
    assert {c.value for c in Check} <= set(re.findall(r"`(\w+)`", ref))
