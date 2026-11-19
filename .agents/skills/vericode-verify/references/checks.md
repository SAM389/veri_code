# VeriCode checks: meaning and fix

Every finding has a `check` id. Facts in `reason` come from PyPI, pypistats or the AST.
`fix_hint` is a suggestion.

| `check` | Severity | Meaning | Fix |
|---|---|---|---|
| `package_exists` | BLOCK | An import or declared dependency names a package that isn't on PyPI. | Remove it, use the real package from "Did you mean X?", or write the code without it. |
| `package_exists` | WARN | Same, but the import is optional (inside `try/except ImportError` or `if TYPE_CHECKING:`), or the package name was guessed from a namespace import such as `google.*`. | Check the name. If it's meant to be optional, leave it and tell the user. |
| `version_exists` | BLOCK | A pinned version (`==X`) doesn't exist for that package. | Pin a version that exists. The hint names the latest one. |
| `version_exists` | WARN | The pinned version exists but was yanked by its maintainers. | Move to a version that isn't yanked. |
| `near_miss` | WARN | The name is 1–2 letters away from a popular package, and the package itself looks risky (new, few downloads, or only one release). This is how typo-squatting attacks work. | Almost always: switch to the popular package the hint names. |
| `risk` | WARN | The package exists but is very new or rarely downloaded. | Tell the user. Prefer a well-known alternative if one exists. |
| `undeclared` | WARN | The code imports a real package that no dependency file declares. | Add it to `requirements.txt` / `pyproject.toml`. |
| `could_not_verify` | WARN | VeriCode couldn't check something (PyPI unreachable, rate-limited, or a file wouldn't parse). | Not a pass. Fix the syntax error if there is one, otherwise run again later and tell the user. |
| `symbol_exists` | — | Reserved for a future function/attribute check. Not produced yet. | — |

## Verdict

- `BLOCK` if any finding is BLOCK, `WARN` if any is WARN, otherwise `PASS`.
- In diff mode only findings on added lines are reported, plus `could_not_verify`.

## What VeriCode skips on purpose

- Standard library modules and the project's own local modules are never looked up.
- Names listed under `allow:` in `.vericode/config.yaml`, and paths under `ignore_paths:`,
  are skipped. Only the user should change that file.
