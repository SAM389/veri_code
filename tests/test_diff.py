from vericode.diff import parse_unified_diff

DIFF = """\
diff --git a/app/routes.py b/app/routes.py
index 1111111..2222222 100644
--- a/app/routes.py
+++ b/app/routes.py
@@ -1,3 +1,4 @@
 import os
+import fastapi_auth_helpers
 import requests
-import json
+++ an added line whose content starts with "++ "
@@ -10,1 +11,3 @@ def f():
     x = 1
+    y = 2
+++ added line that starts with two plus signs
diff --git a/requirements.txt b/requirements.txt
new file mode 100644
--- /dev/null
+++ b/requirements.txt
@@ -0,0 +1,2 @@
+requests==2.31.0
+fastapi
\\ No newline at end of file
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1 +0,0 @@
-import gone
"""


def test_parse_unified_diff() -> None:
    changed = parse_unified_diff(DIFF)
    assert set(changed) == {"app/routes.py", "requirements.txt"}
    # hunk 1: "+import fastapi_auth_helpers" is new line 2; the "+++ lookalike" line is
    # an added line in hunk 1 (new line 4). Hunk 2 starts at new line 11.
    assert changed["app/routes.py"] == {2, 4, 12, 13}
    assert changed["requirements.txt"] == {1, 2}


def test_plain_diff_without_git_headers() -> None:
    text = "--- a.py\t2026-11-16\n+++ a.py\t2026-11-16\n@@ -1 +1,2 @@\n import x\n+import y\n"
    assert parse_unified_diff(text) == {"a.py": {2}}


def test_empty_diff() -> None:
    assert parse_unified_diff("") == {}
