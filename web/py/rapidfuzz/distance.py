from __future__ import annotations


class Levenshtein:
    @staticmethod
    def distance(a: str, b: str, *, score_cutoff: int | None = None) -> int:
        """Edit distance (insert/delete/substitute). With `score_cutoff`, any distance above it
        is returned as `score_cutoff + 1` (same contract as rapidfuzz)."""
        if score_cutoff is not None and abs(len(a) - len(b)) > score_cutoff:
            return score_cutoff + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
            if score_cutoff is not None and min(cur) > score_cutoff:
                return score_cutoff + 1
            prev = cur
        d = prev[-1]
        if score_cutoff is not None and d > score_cutoff:
            return score_cutoff + 1
        return d
