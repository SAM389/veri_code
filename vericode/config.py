"""Per-project settings from `.vericode/config.yaml`.

```yaml
allow: [acme-internal, acme_utils]   # never looked up on PyPI, never reported
ignore_paths: ["scripts/*", "legacy/"]
```

An invalid file raises `ConfigError` instead of falling back to defaults: silently dropping
the allowlist would send private package names to PyPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from vericode.registry import normalize

CONFIG_FILE = Path(".vericode") / "config.yaml"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    allow: frozenset[str] = frozenset()  # PEP 503 normalized names
    ignore_paths: tuple[str, ...] = ()  # glob or directory prefix, relative to root, POSIX

    def is_allowed(self, name: str) -> bool:
        return normalize(name) in self.allow

    def is_ignored(self, rel: str) -> bool:
        for pat in self.ignore_paths:
            prefix = pat.rstrip("/")
            if fnmatchcase(rel, pat) or rel == prefix or rel.startswith(prefix + "/"):
                return True
        return False


def load_config(root: Path) -> Config:
    path = root / CONFIG_FILE
    if not path.is_file():
        return Config()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Could not read {CONFIG_FILE.as_posix()}: {e}") from e
    if data is None:
        return Config()
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_FILE.as_posix()} must be a mapping")
    unknown = set(data) - {"allow", "ignore_paths"}
    if unknown:
        raise ConfigError(f"{CONFIG_FILE.as_posix()}: unknown keys {sorted(unknown)}")
    allow = _str_list(data, "allow")
    ignore = _str_list(data, "ignore_paths")
    return Config(
        allow=frozenset(normalize(a) for a in allow),
        ignore_paths=tuple(p.replace("\\", "/").removeprefix("./") for p in ignore),
    )


def _str_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{CONFIG_FILE.as_posix()}: '{key}' must be a list of strings")
    return value
