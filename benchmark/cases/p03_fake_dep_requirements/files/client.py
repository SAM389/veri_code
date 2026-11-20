import httpx


def get(url: str) -> dict:
    return httpx.get(url, timeout=10).json()
