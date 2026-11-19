# AGENTS.md: VeriCode

> Shared instructions for every coding agent (Claude Code, Gemini CLI, Antigravity, ...).
> `CLAUDE.md` and `GEMINI.md` only import this file, so edit this file, not them.

VeriCode checks code written by AI coding agents for **made-up packages, versions and APIs**, and returns structured findings (PASS / WARN / BLOCK + fix hints) so the agent can repair its own change. Python 3.11+, published on PyPI as **`vericode-ai`**. The import package and CLI are both `vericode`. Source-available under MIT + Commons Clause (no selling without the owner's permission).

**Read this file first. It should be enough to know which file and function to edit. Open only the files you're changing.**

## Status (update as phases land)
Plan docs: `plan_doc/VERICODE_PLAN.md` (why/what) and `plan_doc/vericode_phases.md` (phase-by-phase spec with exit gates).

| Phase | What | State |
|---|---|---|
| P0 | Scaffold, packaging, CI | ✅ done |
| P1 | Findings model, import/dep extraction, PyPI registry + cache, diff input, CLI | ✅ done |
| P2 | `mapping.py` (import→package names, local-module filter, allowlist), `config.py`, `risk.py` (risk signals, near-miss), undeclared check | ✅ done |
| P3 | `symbols.py` (sandbox venv + pyright) | ⏳ stub |
| P4 | `mcp_server.py` (`verify_change` + `verify_files` tools, stdio + streamable HTTP) | ✅ done |
| P5 | `benchmark/run.py` + `benchmark/cases/` | ⏳ stub |
| P6–P7 | README/GIF, CI matrix, release, launch | ⏳ |

P2 decisions that differ from or go beyond the plan doc: declared deps are tried *before* the bundled map (the project's own declaration is the most specific evidence); a risk WARN needs one strong signal (first release < 30 days, < 1,000 downloads/month) or both weak ones (single release + no links); a 404 on a name guessed from a namespace import is WARN, not BLOCK; an invalid `.vericode/config.yaml` is an error (exit 3), never silently ignored, so allowlisted names can't leak.

## Commands
```bash
uv venv --python 3.11 .venv && uv pip install -e ".[dev]"   # setup (Windows: .venv/Scripts/activate)
pytest                      # fast offline suite (slow tests excluded by default via addopts)
pytest -m slow              # live tests against real PyPI (tests/test_live_pypi.py)
ruff check . && ruff format --check .   # lint; line length 100; tests/fixtures + benchmark/cases excluded
vericode check --root tests/fixtures/fake_import            # manual smoke test -> BLOCK, exit 1
vericode check --root . --diff change.patch | --diff -      # diff mode (file or stdin)
vericode-mcp                                                # MCP over stdio (local agents)
vericode-mcp --transport http --host 0.0.0.0 --port 8080  # MCP over HTTP at /mcp, health at /health
```
Set `VERICODE_CACHE_DIR=<dir>` to keep the real PyPI cache out of experiments.

## Data flow
```
cli.check() ──► engine.run_check(root, paths, diff, registry=None, use_cache=True, config=None) -> Report
                  0. config.load_config(root)  (.vericode/config.yaml: allow, ignore_paths; invalid -> ConfigError)
                  1. pick files: diff.parse_unified_diff() (diff mode) or extract.discover() (path mode),
                     minus config.ignore_paths
                  2. engine._collect(): extract.extract_imports() + third_party()   for .py
                                        extract.parse_pyproject() / parse_requirements() for dep files
                                        parse errors -> COULD_NOT_VERIFY WARN finding
                     engine._declared(): scanned deps + root dep files (also in diff mode)
                  3. mapping.Resolver.resolve(ref) -> [Resolved(package, source, path)]
                     (drops stdlib / local modules / allowlisted / bare namespace roots; allowlisted deps dropped too)
                  4. registry.Registry.lookup_many(package names)  (async, cached)
                     + Registry.downloads_many(existing packages not in risk.top_packages())  (pypistats, soft)
                  5. engine._import_findings() + _dep_findings() (+ _version_finding()) + _risk_findings()
                     + _undeclared_findings()
                  6. diff mode: keep only findings on added lines (engine._on_added_line)
             ◄── Report (verdict.py) -> text (cli._print_text) or JSON (Report.to_json)
```
Every entry point (CLI, MCP server, benchmark in P5) must go through `engine.run_check`. Don't duplicate the pipeline.

Checks per package: NotFound → `package_exists` BLOCK (WARN if optional import or namespace guess), with "Did you mean X?" from `risk.near_miss(allow_variants=True)`. Exists and not in the top list → `risk.assess()`; if flagged: `near_miss` WARN when the name is ≤2 edits from a popular package, else `risk` WARN. A near-miss on a healthy package (httpie/httpx) is not reported. Real, required, resolved import missing from every dep file → `undeclared` WARN (skipped when the project has no dep files).

## File map

### `vericode/` (the package)
| File | Responsibility | Key names |
|---|---|---|
| `__init__.py` | `__version__` from installed metadata (`vericode-ai`), `"0.0.0"` from a bare checkout | `__version__` |
| `verdict.py` | **Shared schema** (CLI, MCP, benchmark all depend on it). Severity ordering, check ids, finding, report → JSON | `Severity` (PASS/WARN/BLOCK, `.rank`), `Check` (`package_exists`, `version_exists`, `risk`, `near_miss`, `undeclared`, `symbol_exists`, `could_not_verify`), `Finding` (frozen: check, severity, file, line, package, symbol, reason, fix_hint; `.to_dict()` drops empty fields), `verdict()`, `sort_findings()` (BLOCK first, then file/line), `Report` (findings, files_checked, packages_checked, duration_s; `.verdict`, `.count()`, `.to_json()` → `{verdict, findings[], stats{block, warn, files_checked, packages_checked, duration_s}}`) |
| `extract.py` | Read files → `ImportRef` / `DeclaredDep`; file discovery | `ImportRef(module, file, line, optional, names)` + `.top_level`, `.paths` (`from google.cloud import storage` → `("google.cloud.storage",)`; `*` dropped); `DeclaredDep(name, specifier, file, line)` + `.pinned_version` (only `==X`/`===X`, no wildcard); `extract_imports(path, rel)` (ast; skips relative imports; raises SyntaxError); `_ImportVisitor` (marks `optional` inside `try` whose handler catches ImportError/ModuleNotFoundError/Exception/BaseException/bare, or `if TYPE_CHECKING:`); `third_party()` (drops `STDLIB` = `sys.stdlib_module_names` + `__future__`); `parse_requirements()` (skips `-r/-e/--opts`, URLs, paths, `pkg @ url`, invalid lines; handles `\` continuations and inline comments); `parse_pyproject()` (`[project].dependencies`, `optional-dependencies`, `[dependency-groups]`, `[tool.poetry.*dependencies]`; line numbers are best-effort via `_find_line` because tomllib has no positions); `_poetry_specifier()` (only exact versions become pins); `discover(paths)` / `_walk()` (yields `.py` + dep files; skips `SKIP_DIRS`, hidden dirs, any dir with `pyvenv.cfg`); `is_dependency_file()`, `is_requirements_file()` (`requirements*.txt`) |
| `registry.py` | PyPI JSON API client + on-disk TTL cache. **Only source of package facts** | `normalize()` (PEP 503), `is_valid_name()`; results are a union `LookupResult = PackageInfo \| NotFound \| Unknown`; `PackageInfo(name, latest_version, releases{ver: Release(yanked, upload_time)}, home_page, project_urls)` + `.version_status(v)` → `present/yanked/missing` (PEP 440 equality, so `2.31 == 2.31.0`), `.from_pypi()`, `.to_cache()/.from_cache()`; `Cache(path, now=time.time)` (JSON file `<cache_dir>/pypi.json`; `FOUND_TTL_S`=24h, `NOT_FOUND_TTL_S`=1h; never stores `Unknown`; atomic save; corrupt file → empty; `.default()`, `.disabled()`); `default_cache_dir()` (`$VERICODE_CACHE_DIR` or platformdirs); `Registry(cache, client=None, concurrency=10, timeout_s=5, retries=1)`: async context manager (saves cache on exit), `lookup()`, `lookup_many()` (keys = normalized names), `_fetch()` (404→NotFound, 200→PackageInfo, timeout/transport/5xx retried once, 429/other 4xx → Unknown with no retry); pypistats: `downloads_last_month()` / `downloads_many()` (`PYPISTATS_URL`; any failure → `None`, no retry, not cached; successes cached 24h via `Cache.get_downloads/put_downloads` under `downloads:<name>` keys in the same file). `USER_AGENT = "vericode/<ver>"` (keep it generic, no personal info) |
| `diff.py` | Unified diff → `{posix_path: {added new-file line numbers}}` | `parse_unified_diff(text)`: uses `@@` hunk counts to know where a hunk ends (content like `+++ x` is never mistaken for a header); skips deleted files (`+++ /dev/null`); strips `a/`/`b/`, quotes, tab timestamps |
| `engine.py` | Orchestration + **all finding wording** (reason/fix_hint text lives here) | `run_check()`; `_collect()` → `_Collected(files, imports, deps, findings)`; `_declared()` → `_Declared(names, files)`; `_Use(ref, resolved)`; `_import_findings()` (NotFound → BLOCK, or WARN if `optional` or namespace guess; near-miss hint; Unknown → one `could_not_verify` per package; `_stmt()` renders the import); `_dep_findings()` (NotFound → BLOCK; pinned → `_version_finding`: missing → BLOCK, yanked → WARN, hint names `latest_version`); `_risk_findings()` (one `near_miss`/`risk` WARN per package; `_signal_text()` words the signals); `_undeclared_findings()`; `_locations()` / `_first()` (per-package findings go on the first use, preferring added lines in diff mode); `_could_not_verify()`, `_unparsable()`; `_on_added_line()` (diff filter; `could_not_verify` always kept); `_rel()` (paths relative to root, POSIX) |
| `cli.py` | Typer app, thin wrapper over the engine | `app`; `check(paths, --diff FILE\|-, --root, --format text\|json, --fail-on block\|warn, --no-cache)`; exit codes `EXIT_OK=0` (below threshold), `EXIT_FAILED=1` (verdict ≥ `--fail-on`), `EXIT_INTERNAL_ERROR=3` (any exception, incl. `ConfigError` → "invalid config"; message on stderr); `_print_text()` (ASCII only: `->` and `\|`, safe on Windows cp1252 pipes) |
| `mcp_server.py` | MCP server (mcp SDK 2.x `MCPServer`, not v1 `FastMCP`). Thin wrapper over `engine.run_check`; tool results are `Report.to_json()`; config/internal errors → `ToolError` (an error result, never BLOCK) | `build_server(local=True)`: `verify_change(root, diff=None, paths=None)` (local only: reads the caller's disk) + `verify_files(files{relpath: content}, diff=None)` (writes to a temp dir; `_safe_rel` rejects absolute/`..`/drive paths; `_validate_files` caps `MAX_FILES`, `MAX_TOTAL_BYTES`); `local=False` (HTTP) exposes only `verify_files`; `GET /health`; `INSTRUCTIONS` (what the agent should do per verdict); `main(argv)`: `--transport stdio\|http`, `--host` (`$HOST`), `--port` (`$PORT`); HTTP is stateless + JSON responses (Cloud Run friendly); log level WARNING so package names aren't logged. Entry point `vericode-mcp` |
| `config.py` | `.vericode/config.yaml` loader | `Config(allow: normalized frozenset, ignore_paths)` + `.is_allowed()`, `.is_ignored(rel)` (fnmatch or dir prefix); `load_config(root)`; `ConfigError` (bad YAML, wrong types, unknown keys) |
| `mapping.py` | Import → PyPI package, and what must never be looked up | `Resolver(root, declared=, allow=, installed=None, imports=None)`: `.resolve(ref)` → `[Resolved(package, source, path)]` (`.guessed`), `.resolve_path()`, `.skip()` (stdlib, allowlist, `.is_local()`: root + `src/` modules, project's own name, any dir between the file and root); order: `installed` (`installed_distributions()`, skipped for namespaces, ambiguous → prefers declared) → `declared` (name, `py`/`py-`/`python-` prefix, `-python`/`-py` suffix) → `map` (`import_map()`, longest dotted prefix, case-sensitive keys) → `namespace` guess (`NAMESPACES`) → `import-name`. Bare namespace roots (`import google`) → nothing |
| `risk.py` | Near-miss + risk signals (pure functions over registry facts) | `top_packages()` → `TopPackages(ordered, names)`; `near_miss(name, top, allow_variants=False)` → `NearMiss(candidate, distance)` (rapidfuzz Levenshtein ≤2; skips names <5 chars and names in the list; without `allow_variants` skips separator/`py` prefix variants); `assess(info, downloads, now)` → `RiskSignals` (`.new`, `.low_downloads`, `.single_release`, `has_links`, `.flagged`); constants `NEW_PACKAGE_DAYS`, `LOW_DOWNLOADS` |
| `symbols.py` | Docstring-only stub for P3 | see `plan_doc/vericode_phases.md` §3 |
| `data/import_map.json` | ~200 import → PyPI name mismatches (`cv2`, `PIL`, `google.cloud.storage`, …); every value verified to exist (`test_live_import_map_targets_exist`) | shipped in the wheel |
| `data/top_packages.txt` | Top 5,000 PyPI projects by 30-day downloads, normalized, most popular first; header has the source + snapshot date (hugovk/top-pypi-packages) | shipped in the wheel |

### `tests/`
| File | Covers |
|---|---|
| `conftest.py` | **Test harness.** autouse `_isolated_cache` (sets `VERICODE_CACHE_DIR` to tmp); autouse `_no_installed_metadata` (mapping ignores the test env's installed packages); `pypi` fixture = `FakePyPI` mounted with respx on `PYPI_ROUTE` (known names → 200, everything else → 404; `.calls` records looked-up names) and `PYPISTATS_ROUTE` (`.downloads[name] = n` → 200, else 404; `.stats_calls`); `make_payload(name, versions, yanked=, upload_time=)`; `DEFAULT_PACKAGES` = requests (2.30.0 yanked, 2.31.0, 2.32.3), fastapi, httpx; `fixtures` fixture → `tests/fixtures/` |
| `test_verdict.py` | severity ordering, sorting, JSON shape |
| `test_extract.py` | import forms, `names`/`paths`, relative/stdlib skip, optional detection, requirements/pyproject/poetry parsing, `pinned_version`, discovery skips |
| `test_registry.py` | normalize, found/404, version_status, timeout/5xx/429 behaviour, cache hit/TTLs (injectable `Clock`), Unknown not cached, corrupt cache, invalid names, pypistats downloads (cached, failures → None) |
| `test_diff.py` | multi-file git diff, new/deleted files, `++` content lines, plain `diff -u` |
| `test_engine.py` | end-to-end on fixtures + tmp projects: BLOCK/PASS, bad/yanked pins, fake declared dep, optional import → WARN, PyPI down → WARN only, syntax error, diff filtering; P2: exit gate (mapped imports PASS, no stdlib/local lookups), did-you-mean, near_miss/risk WARNs, healthy near-miss silent, pypistats down, undeclared (incl. diff mode), namespace guess → WARN, allowlist/ignore_paths, invalid config |
| `test_cli.py` | exit codes, text/JSON output, `--fail-on warn`, `--diff` file + stdin, internal error → 3, invalid config → 3 |
| `test_mapping.py` | map cases, namespaces (mapped, guessed, bare root), installed/declared resolution, local-module shadowing, allowlist |
| `test_risk.py` | top list, near-miss positives/negatives (short, popular, variants), `assess` signals and the flag rule |
| `test_config.py` | defaults, allow/ignore_paths, invalid configs raise |
| `test_mcp.py` | in-process `mcp.Client` against `build_server()`: tool lists (remote has no `verify_change`), BLOCK/PASS via both tools, diff mode, bad root, path-escape + size limits, invalid config → error, PyPI down → WARN |
| `test_smoke.py` | import, `--help`, `--version` |
| `test_live_pypi.py` | `@pytest.mark.slow`, real PyPI: fixtures, every `import_map.json` target exists, mapped imports PASS, typo gets a hint |
| `fixtures/fake_import/` | `app/routes.py` imports nonexistent `fastapi_auth_helpers` → BLOCK |
| `fixtures/clean_requests/` | stdlib + `requests==2.31.0` → PASS |
| `fixtures/bad_version/` | `requests==99.0.0` → BLOCK `version_exists` |

### Other
- `pyproject.toml`: hatchling; deps; extras `[symbols]` (pyright) and `[dev]`; scripts `vericode`, `vericode-mcp`; pytest (`asyncio_mode=auto`, `slow` marker, `-m 'not slow'`); ruff (`E,F,W,I,B,UP,SIM,RUF`, line 100).
- `.github/workflows/ci.yml`: ubuntu, py3.11, uv install, ruff + pytest (matrix arrives in P6).
- `benchmark/run.py` (stub), `benchmark/cases/` (empty, P5).
- `LICENSE`: Commons Clause + modified MIT with no right to sell. Don't change it without the owner asking.

## Where to change X
| Change | Edit | Test in |
|---|---|---|
| Wording of a finding's reason/fix hint | `engine.py` (`_import_findings`, `_dep_findings`, `_version_finding`, `_risk_findings`, `_signal_text`, `_undeclared_findings`, `_could_not_verify`, `_unparsable`) | `test_engine.py`, `test_cli.py` (asserts on text) |
| New check type | add to `Check` in `verdict.py` → produce findings in a new module → call it from `engine.run_check` | new `tests/test_<module>.py` + `test_engine.py` |
| Severity rules / verdict logic | `verdict.py` (`Severity`, `_RANK`, `verdict`) | `test_verdict.py` |
| JSON output shape | `Finding.to_dict`, `Report.to_json` (**breaking**: MCP/benchmark consume it) | `test_verdict.py`, `test_cli.py::test_json_output` |
| Text output format | `cli._print_text` | `test_cli.py` |
| New CLI flag | `cli.check` signature → pass through to `run_check` | `test_cli.py` |
| Which files get scanned / skipped dirs | `extract.SKIP_DIRS`, `_walk`, `discover` | `test_extract.py::test_discover_*` |
| Import parsing / optional-import rules | `extract._ImportVisitor`, `_OPTIONAL_EXC`, `_is_type_checking` | `test_extract.py` |
| Stdlib list | `extract.STDLIB`, `third_party` | `test_extract.py` |
| New dependency-file format | add `parse_<fmt>()` in `extract.py`, extend `is_dependency_file`, dispatch in `engine._collect` | `test_extract.py`, `test_engine.py` |
| PyPI request behaviour (timeouts, retries, concurrency, UA) | `registry.Registry.__init__` / `_fetch` | `test_registry.py` |
| Cache TTLs / location / format | `registry.FOUND_TTL_S`, `NOT_FOUND_TTL_S`, `Cache`, `default_cache_dir` | `test_registry.py::test_cache_*` |
| Version matching (pins, yanked) | `extract.DeclaredDep.pinned_version`, `registry.PackageInfo.version_status` | `test_extract.py`, `test_registry.py` |
| Diff parsing / diff-mode filtering | `diff.parse_unified_diff`; `engine._on_added_line` | `test_diff.py`, `test_engine.py::test_diff_*` |
| Import name → PyPI name | add to `data/import_map.json` (value must exist on PyPI; run `pytest -m slow`); namespaces in `mapping.NAMESPACES`; order in `Resolver.resolve_path` | `test_mapping.py`, `test_live_pypi.py` |
| Local-module / allowlist filtering | `mapping.Resolver.is_local`, `_dir_modules`, `skip`; config format in `config.py` | `test_mapping.py`, `test_config.py`, `test_engine.py` |
| Risk thresholds / near-miss rules | `risk.py` (`NEW_PACKAGE_DAYS`, `LOW_DOWNLOADS`, `RiskSignals.flagged`, `near_miss`, `_is_variant`) | `test_risk.py`, `test_engine.py` |
| Refresh the top-packages snapshot | regenerate `data/top_packages.txt` from hugovk/top-pypi-packages (first 5,000, PEP 503 normalized) and update the date in its header | `test_risk.py` |
| Undeclared rule / which dep files count | `engine._declared`, `_undeclared_findings` | `test_engine.py::test_*undeclared*` |
| Symbol/API check (P3) | `symbols.py`; findings `Check.SYMBOL_EXISTS`; add `--no-symbols` flag in `cli.check` | new `test_symbols.py` (recorded pyright JSON) |
| MCP tools / transports | `mcp_server.py` (`build_server`, `main`); tool wording in the `description=` strings and `INSTRUCTIONS` | `test_mcp.py` |

## Invariants (don't break these)
1. **Facts come from real tools.** `Finding.reason` must come from PyPI, pypistats, pyright or the AST. An LLM may only ever write `fix_hint`.
2. **Fail open.** Any VeriCode-side failure (network, 429, 5xx, unreadable file) → `Check.COULD_NOT_VERIFY` with **WARN**, never BLOCK. `Unknown` is never cached.
3. **Schema stability.** `Finding` fields, `Check` values and `Report.to_json()` keys are a public contract. Add fields; don't rename or remove them.
4. **One pipeline.** New entry points call `engine.run_check`. Don't reimplement extraction or lookups.
5. **Offline tests.** The default `pytest` never touches the network. Mock PyPI with the `pypi` fixture / respx. Anything live gets `@pytest.mark.slow`.
6. **Privacy.** Don't send personal or project info to external services (the User-Agent stays generic). Allowlisted names and local module names must never be looked up (on PyPI or pypistats).
7. **Security (P3).** Never `pip install` a package that failed the package check. Wheels only by default.
8. **Normalization.** Compare package names only after `registry.normalize()`, and versions with `packaging.version.Version`.
9. **Paths** in findings are POSIX and relative to `root` (`engine._rel`).

## Conventions
- `from __future__ import annotations`, full type hints, frozen dataclasses for value objects, `StrEnum` for closed sets.
- Async only where the network is involved (`Registry`); the CLI calls `asyncio.run(run_check(...))`.
- Output must stay ASCII-safe for Windows consoles.
- Planted fake package names in tests/benchmark must be invented, never copied from real agent output (attackers could register them).
- Commit/push only when the user asks.

## Before you finish a change
Run all three and fix what they report:
```bash
pytest
ruff check . && ruff format --check .
git diff HEAD | vericode check --root . --diff -   # VeriCode checks its own change; fix any BLOCK
```
(`git diff HEAD` skips untracked files; `git add -N <file>` new files first so they are checked too.)

## Keep this file current
When you add, rename or remove a module, a public function, a `Check` value, a CLI flag or a test fixture, update the matching row in `AGENTS.md` in the same change, and update the **Status** table when a phase lands. Never put project content in `CLAUDE.md` or `GEMINI.md`; they stay one-line imports of `AGENTS.md`.
