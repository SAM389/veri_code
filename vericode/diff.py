"""Minimal unified-diff parser: which files changed, and which new-file lines were added."""

from __future__ import annotations

import re

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(text: str) -> dict[str, set[int]]:
    """Map each changed file (POSIX path, relative to the repo root) to its added line numbers.

    Deleted files are omitted. Lines are numbered in the post-change file. Hunk lengths from
    the `@@` headers decide where a hunk ends, so content lines that look like headers
    (e.g. an added line "++ x") are never misread.
    """
    changed: dict[str, set[int]] = {}
    current: set[int] | None = None
    old_left = new_left = 0
    new_line = 0

    for raw in text.splitlines():
        if old_left > 0 or new_left > 0:  # inside a hunk
            tag = raw[:1]
            if tag == "+":
                if current is not None:
                    current.add(new_line)
                new_line += 1
                new_left -= 1
            elif tag == "-":
                old_left -= 1
            elif tag == "\\":
                pass  # "\ No newline at end of file"
            else:  # context line (" " or an empty line some tools emit)
                new_line += 1
                new_left -= 1
                old_left -= 1
            continue

        if raw.startswith("+++ "):
            path = _strip_path(raw[4:])
            current = None if path is None else changed.setdefault(path, set())
        elif m := _HUNK.match(raw):
            old_left = int(m.group(1)) if m.group(1) is not None else 1
            new_line = int(m.group(2))
            new_left = int(m.group(3)) if m.group(3) is not None else 1
    return changed


def _strip_path(header: str) -> str | None:
    path = header.split("\t", 1)[0].strip()
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith(("b/", "a/")):
        path = path[2:]
    return path.replace("\\", "/")
