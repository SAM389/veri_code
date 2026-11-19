---
name: vericode-verify
description: Verify Python code against PyPI for made-up packages, fake or yanked version pins, typo-squat lookalikes and undeclared dependencies, then fix what it finds. Use after you add or change Python imports, requirements*.txt or pyproject.toml dependencies, and before you say a Python change is done.
---

# VeriCode: check your change, fix every BLOCK

You can make up package names and versions without noticing. VeriCode checks them against
the real PyPI and the code's AST, not against a model, so **trust its `reason` over your
own memory**.

## When to run it

- After any edit that adds or changes an `import` / `from ... import` in a `.py` file.
- After any edit to `requirements*.txt` or `pyproject.toml` dependencies.
- Before you tell the user a Python change is finished. Run it once more after your fixes.

## How to run it (use the first one that's available)

1. **MCP tool `verify_change`** (local VeriCode server):
   - `root`: absolute path of the workspace.
   - `diff`: output of `git diff HEAD`. Run `git add -N <file>` on new files first so
     they show up in the diff. With a diff, only lines you added are reported.
   - No git repo? Leave out `diff` to check the whole project.
2. **MCP tool `verify_files`** (remote VeriCode server, e.g. on Cloud Run):
   - `files`: `{relative/posix/path: full file content}` for every changed `.py` file
     **plus** `requirements*.txt` / `pyproject.toml`, so declared deps are known.
   - `diff`: optional, same as above.
3. **CLI**, if no VeriCode MCP tool is connected:
   ```bash
   git diff HEAD | vericode check --root . --diff - --format json
   ```
   Exit code 0 = no BLOCK, 1 = BLOCK found, 3 = VeriCode itself failed.

If none of these work, tell the user VeriCode isn't set up and stop. Don't install
VeriCode or any other package yourself to get around it.

## What to do with the result

The result is `{verdict, findings: [{check, severity, file, line, package, reason, fix_hint}], stats}`.

- **BLOCK**: fix every BLOCK finding, using its `fix_hint`:
  - Fake package (`package_exists`): remove the import, or switch to the real package
    named in "Did you mean X?", or write the code without a dependency. Never swap in
    another name you haven't verified.
  - Bad pin (`version_exists`): change it to a version that exists (the hint names the
    latest one).

  Then run VeriCode again. Repeat until there are no BLOCKs, at most 3 rounds. If BLOCKs
  are still there after 3 rounds, stop and show the user the remaining findings.
- **WARN**: don't ignore it silently.
  - `near_miss`: make sure you meant this package and not the popular one it resembles.
  - `risk`: very new or rarely downloaded package. Tell the user before you rely on it.
  - `undeclared`: add the package to the project's dependency file.
  - `could_not_verify`: VeriCode couldn't reach PyPI or read a file. Say so. It's not a pass.
- **PASS**: done.

For what each check means and the full fix rules, read `references/checks.md`.

## Never

- Never `pip install` a package to "test" whether it exists. Installing runs its code, and
  a made-up name may already be registered by an attacker.
- Never add names to `.vericode/config.yaml` (`allow:`) or `ignore_paths:` to silence a
  finding, unless the user asks you to.
- Never edit or reword findings when you report them. Quote `reason` as it is.

## Tell the user

One line with the final verdict, then one line per thing you changed because of it. For
example: "VeriCode: PASS (was BLOCK: removed made-up `fastapi_auth_helpers`, used
`fastapi.security` instead)."
