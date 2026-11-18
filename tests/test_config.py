from pathlib import Path

import pytest

from vericode.config import Config, ConfigError, load_config


def _write(root: Path, text: str) -> None:
    (root / ".vericode").mkdir()
    (root / ".vericode" / "config.yaml").write_text(text, encoding="utf-8")


def test_missing_config_is_default(tmp_path: Path) -> None:
    assert load_config(tmp_path) == Config()


def test_empty_config_is_default(tmp_path: Path) -> None:
    _write(tmp_path, "")
    assert load_config(tmp_path) == Config()


def test_allow_and_ignore_paths(tmp_path: Path) -> None:
    _write(tmp_path, "allow: [Acme_Internal, acme.utils]\nignore_paths: ['scripts/*', ./legacy/]\n")
    cfg = load_config(tmp_path)
    assert cfg.allow == {"acme-internal", "acme-utils"}
    assert cfg.is_allowed("ACME.internal")
    assert cfg.is_ignored("scripts/run.py")
    assert cfg.is_ignored("legacy/old/mod.py")
    assert cfg.is_ignored("legacy")
    assert not cfg.is_ignored("legacy_new/mod.py")
    assert not cfg.is_ignored("app/main.py")


@pytest.mark.parametrize(
    "text",
    [
        "allow: acme",  # not a list
        "allow: [1, 2]",  # not strings
        "- a\n- b\n",  # not a mapping
        "alow: [acme]\n",  # typo'd key: must not silently drop the allowlist
        "allow: [acme\n",  # invalid YAML
    ],
)
def test_invalid_config_raises(tmp_path: Path, text: str) -> None:
    _write(tmp_path, text)
    with pytest.raises(ConfigError):
        load_config(tmp_path)
