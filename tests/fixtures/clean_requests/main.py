import json
import os

import requests
from requests.adapters import HTTPAdapter

session = requests.Session()
session.mount("https://", HTTPAdapter(max_retries=3))


def fetch(url: str) -> dict:
    resp = session.get(url, timeout=float(os.environ.get("TIMEOUT", "5")))
    resp.raise_for_status()
    return json.loads(resp.text)
