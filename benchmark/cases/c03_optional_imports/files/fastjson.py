from __future__ import annotations

import json
from typing import TYPE_CHECKING

import requests

try:
    import orjson as _json
except ImportError:
    _json = json

if TYPE_CHECKING:
    import pandas as pd


def fetch(url: str) -> dict:
    return _json.loads(requests.get(url, timeout=10).content)


def to_frame(rows: list[dict]) -> pd.DataFrame: ...
