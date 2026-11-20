from __future__ import annotations

from collections.abc import Callable, Sequence


def extract(
    query: str,
    choices: Sequence[str],
    *,
    scorer: Callable[..., int],
    score_cutoff: int,
    limit: int = 5,
) -> list[tuple[str, int, int]]:
    """(choice, distance, index) for choices within `score_cutoff`, closest first."""
    out = []
    for idx, choice in enumerate(choices):
        dist = scorer(query, choice, score_cutoff=score_cutoff)
        if dist <= score_cutoff:
            out.append((choice, dist, idx))
    out.sort(key=lambda m: (m[1], m[2]))
    return out[:limit]
