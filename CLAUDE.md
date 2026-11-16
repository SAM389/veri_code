# CLAUDE.md: VeriCode

VeriCode checks code written by AI coding agents for **made-up packages, versions and APIs**, and returns structured findings (PASS / WARN / BLOCK + fix hints) so the agent can repair its own change. Python 3.11+, published on PyPI as **`vericode-ai`**. The import package and CLI are both `vericode`. Source-available under MIT + Commons Clause (no selling without the owner's permission).

**Read this file first. It should be enough to know which file and function to edit. Open only the files you're changing.**

## Status (update as phases land)
Plan docs: `plan_doc/VERICODE_PLAN.md` (why/what) and `plan_doc/vericode_phases.md` (phase-by-phase spec with exit gates).

| Phase | What | State |
|---|---|---|
| P0 | Scaffold, packaging, CI | ✅ done |
| P1 | Findings model, import/dep extraction, PyPI registry + cache, diff input, CLI | ✅ done |
| P2 | `mapping.py` (import→package names, local-module filter, allowlist), `risk.py` (risk signals, near-miss) | ⏳ stub files only |
| P3 | `symbols.py` (sandbox venv + pyright) | ⏳ stub |
| P4 | `mcp_server.py` (`verify_change` tool) | ⏳ stub: `main()` exits 3 |
| P5 | `benchmark/run.py` + `benchmark/cases/` | ⏳ stub |
| P6–P7 | README/GIF, CI matrix, release, launch | ⏳ |

Known gap until P2: local packages (e.g. `from tests.conftest import ...`) get looked up on PyPI and BLOCK, and imports are looked up by their own name (`cv2` is not yet mapped to `opencv-python`).

## Commands
```bash
uv venv --python 3.11 .venv && uv pip install -e ".[dev]"   # setup (Windows: .venv/Scripts/activate)
pytest                      # fast offline suite (slow tests excluded by default via addopts)
pytest -m slow              # live tests against real PyPI (tests/test_live_pypi.py)
ruff check . && ruff format --check .   # lint; line length 100; tests/fixtures + benchmark/cases excluded
vericode check --root tests/fixtures/fake_import            # manual smoke test -> BLOCK, exit 1
vericode check --root . --diff change.patch | --diff -      # diff mode (file or stdin)
```
Set `VERICODE_CACHE_DIR=<dir>` to keep the real PyPI cache out of experiments.

## Data flow
```
cli.check() ──► engine.run_check(root, paths, diff, registry=None, use_cache=True) -> Report
                  1. pick files: diff.parse_unified_diff() (diff mode) or extract.discover() (path mode)
                  2. engine._collect(): extract.extract_imports() + third_party()   for .py
                                        extract.parse_pyproject() / parse_requirements() for dep files
                                        parse errors -> COULD_NOT_VERIFY WARN finding
                  3. registry.Registry.lookup_many(normalized names)  (async, cached)
                  4. engine._import_findings() + _dep_findings() (+ _version_finding())
                  5. diff mode: keep only findings on added lines (engine._on_added_line)
             ◄── Report (verdict.py) -> text (cli._print_text) or JSON (Report.to_json)
```
Every entry point (CLI now, MCP in P4, benchmark in P5) must go through `engine.run_check`. Don't duplicate the pipeline.

## File map

### `vericode/` (the package)
| File | Responsibility | Key names |
|---|---|---|
| `__init__.py` | `__version__` from installed metadata (`vericode-ai`), `"0.0.0"` from a bare checkout | `__version__` |
| `verdict.py` | **Shared schema** (CLI, MCP, benchmark all depend on it). Severity ordering, check ids, finding, report → JSON | `Severity` (PASS/WARN/BLOCK, `.rank`), `Check` (`package_exists`, `version_exists`, `risk`, `near_miss`, `undeclared`, `symbol_exists`, `could_not_verify`), `Finding` (frozen: check, severity, file, line, package, symbol, reason, fix_hint; `.to_dict()` drops empty fields), `verdict()`, `sort_findings()` (BLOCK first, then file/line), `Report` (findings, files_checked, packages_checked, duration_s; `.verdict`, `.count()`, `.to_json()` → `{verdict, findings[], stats{block, warn, files_checked, packages_checked, duration_s}}`) |
| `extract.py` | Read files → `ImportRef` / `DeclaredDep`; file discovery | `ImportRef(module, file, line, optional)` + `.top_level`; `DeclaredDep(name, specifier, file, line)` + `.pinned_version` (only `==X`/`===X`, no wildcard); `extract_imports(path, rel)` (ast; skips relative imports; raises SyntaxError); `_ImportVisitor` (marks `optional` inside `try` whose handler catches ImportError/ModuleNotFoundError/Exception/BaseException/bare, or `if TYPE_CHECKING:`); `third_party()` (drops `STDLIB` = `sys.stdlib_module_names` + `__future__`); `parse_requirements()` (skips `-r/-e/--opts`, URLs, paths, `pkg @ url`, invalid lines; handles `\` continuations and inline comments); `parse_pyproject()` (`[project].dependencies`, `optional-dependencies`, `[dependency-groups]`, `[tool.poetry.*dependencies]`; line numbers are best-effort via `_find_line` because tomllib has no positions); `_poetry_specifier()` (only exact versions become pins); `discover(paths)` / `_walk()` (yields `.py` + dep files; skips `SKIP_DIRS`, hidden dirs, any dir with `pyvenv.cfg`); `is_dependency_file()`, `is_requirements_file()` (`requirements*.txt`) |
| `registry.py` | PyPI JSON API client + on-disk TTL cache. **Only source of package facts** | `normalize()` (PEP 503), `is_valid_name()`; results are a union `LookupResult = PackageInfo \| NotFound \| Unknown`; `PackageInfo(name, latest_version, releases{ver: Release(yanked, upload_time)}, home_page, project_urls)` + `.version_status(v)` → `present/yanked/missing` (PEP 440 equality, so `2.31 == 2.31.0`), `.from_pypi()`, `.to_cache()/.from_cache()`; `Cache(path, now=time.time)` (JSON file `<cache_dir>/pypi.json`; `FOUND_TTL_S`=24h, `NOT_FOUND_TTL_S`=1h; never stores `Unknown`; atomic save; corrupt file → empty; `.default()`, `.disabled()`); `default_cache_dir()` (`$VERICODE_CACHE_DIR` or platformdirs); `Registry(cache, client=None, concurrency=10, timeout_s=5, retries=1)`: async context manager (saves cache on exit), `lookup()`, `lookup_many()` (keys = normalized names), `_fetch()` (404→NotFound, 200→PackageInfo, timeout/transport/5xx retried once, 429/other 4xx → Unknown with no retry). `USER_AGENT = "vericode/<ver>"` (keep it generic, no personal info) |
| `diff.py` | Unified diff → `{posix_path: {added new-file line numbers}}` | `parse_unified_diff(text)`: uses `@@` hunk counts to know where a hunk ends (content like `+++ x` is never mistaken for a header); skips deleted files (`+++ /dev/null`); strips `a/`/`b/`, quotes, tab timestamps |
| `engine.py` | Orchestration + **all finding wording** (reason/fix_hint text lives here) | `run_check()`; `_collect()` → `_Collected(files, imports, deps, findings)`; `_import_findings()` (NotFound → BLOCK, or WARN if `optional`; Unknown → one `could_not_verify` per package); `_dep_findings()` (NotFound → BLOCK; pinned → `_version_finding`: missing → BLOCK, yanked → WARN, hint names `latest_version`); `_could_not_verify()`, `_unparsable()`; `_on_added_line()` (diff filter; `could_not_verify` always kept); `_rel()` (paths relative to root, POSIX) |
| `cli.py` | Typer app, thin wrapper over the engine | `app`; `check(paths, --diff FILE\|-, --root, --format text\|json, --fail-on block\|warn, --no-cache)`; exit codes `EXIT_OK=0` (below threshold), `EXIT_FAILED=1` (verdict ≥ `--fail-on`), `EXIT_INTERNAL_ERROR=3` (any exception; message on stderr); `_print_text()` (ASCII only: `->` and `\|`, safe on Windows cp1252 pipes) |
| `mcp_server.py` | P4 stub. `main()` prints "not implemented" and exits 3 | entry point `vericode-mcp` |
| `mapping.py`, `risk.py`, `symbols.py` | Docstring-only stubs for P2/P3 | see `plan_doc/vericode_phases.md` §2–3 |
| `data/import_map.json` | `{}`, to be filled in P2 (import name → PyPI name) | shipped in the wheel |
| `data/top_packages.txt` | empty, to be filled in P2 (top ~5k PyPI names for near-miss) | shipped in the wheel |

### `tests/`
| File | Covers |
|---|---|
| `conftest.py` | **Test harness.** autouse `_isolated_cache` (sets `VERICODE_CACHE_DIR` to tmp); `pypi` fixture = `FakePyPI` mounted with respx on `PYPI_ROUTE` (known names → 200, everything else → 404; `.calls` records looked-up names); `make_payload(name, versions, yanked=, upload_time=)`; `DEFAULT_PACKAGES` = requests (2.30.0 yanked, 2.31.0, 2.32.3), fastapi, httpx; `fixtures` fixture → `tests/fixtures/` |
| `test_verdict.py` | severity ordering, sorting, JSON shape |
| `test_extract.py` | import forms, relative/stdlib skip, optional detection, requirements/pyproject/poetry parsing, `pinned_version`, discovery skips |
| `test_registry.py` | normalize, found/404, version_status, timeout/5xx/429 behaviour, cache hit/TTLs (injectable `Clock`), Unknown not cached, corrupt cache, invalid names |
| `test_diff.py` | multi-file git diff, new/deleted files, `++` content lines, plain `diff -u` |
| `test_engine.py` | end-to-end on fixtures + tmp projects: BLOCK/PASS, bad/yanked pins, fake declared dep, optional import → WARN, PyPI down → WARN only, syntax error, diff filtering |
| `test_cli.py` | exit codes, text/JSON output, `--fail-on warn`, `--diff` file + stdin, internal error → 3 |
| `test_smoke.py` | import, `--help`, `--version` |
| `test_live_pypi.py` | `@pytest.mark.slow`, real PyPI |
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
| Wording of a finding's reason/fix hint | `engine.py` (`_import_findings`, `_dep_findings`, `_version_finding`, `_could_not_verify`, `_unparsable`) | `test_engine.py`, `test_cli.py` (asserts on text) |
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
| Import name → PyPI name (P2) | `mapping.py` + `data/import_map.json`; call it in `engine` where `normalize(ref.top_level)` is used today (`run_check` names set + `_import_findings`) | new `test_mapping.py` |
| Local-module / allowlist filtering (P2) | `mapping.py`, applied in `engine._collect` before lookups | `test_mapping.py`, `test_engine.py` |
| Risk signals / near-miss (P2) | `risk.py` + `data/top_packages.txt`; findings `Check.RISK` / `Check.NEAR_MISS` | new `test_risk.py` |
| Symbol/API check (P3) | `symbols.py`; findings `Check.SYMBOL_EXISTS`; add `--no-symbols` flag in `cli.check` | new `test_symbols.py` (recorded pyright JSON) |
| MCP tool (P4) | `mcp_server.py`, wrapping `engine.run_check` | new `test_mcp.py` |

## Invariants (don't break these)
1. **Facts come from real tools.** `Finding.reason` must come from PyPI, pyright or the AST. An LLM may only ever write `fix_hint`.
2. **Fail open.** Any VeriCode-side failure (network, 429, 5xx, unreadable file) → `Check.COULD_NOT_VERIFY` with **WARN**, never BLOCK. `Unknown` is never cached.
3. **Schema stability.** `Finding` fields, `Check` values and `Report.to_json()` keys are a public contract. Add fields; don't rename or remove them.
4. **One pipeline.** New entry points call `engine.run_check`. Don't reimplement extraction or lookups.
5. **Offline tests.** The default `pytest` never touches the network. Mock PyPI with the `pypi` fixture / respx. Anything live gets `@pytest.mark.slow`.
6. **Privacy.** Don't send personal or project info to external services (the User-Agent stays generic). Allowlisted/private names (P2) must never be looked up.
7. **Security (P3).** Never `pip install` a package that failed the package check. Wheels only by default.
8. **Normalization.** Compare package names only after `registry.normalize()`, and versions with `packaging.version.Version`.
9. **Paths** in findings are POSIX and relative to `root` (`engine._rel`).

## Conventions
- `from __future__ import annotations`, full type hints, frozen dataclasses for value objects, `StrEnum` for closed sets.
- Async only where the network is involved (`Registry`); the CLI calls `asyncio.run(run_check(...))`.
- Output must stay ASCII-safe for Windows consoles.
- Planted fake package names in tests/benchmark must be invented, never copied from real agent output (attackers could register them).
- Commit/push only when the user asks.

## Keep this file current
When you add, rename or remove a module, a public function, a `Check` value, a CLI flag or a test fixture, update the matching row in this file in the same change, and update the **Status** table when a phase lands.
