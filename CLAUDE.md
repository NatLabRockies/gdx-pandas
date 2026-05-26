# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package does

`gdxpds` translates between GDX (GAMS Data eXchange) files and pandas DataFrames. GDX is the binary file format used by [GAMS](https://www.gams.com/), a mathematical optimization modeling system. Two entry points:

- High-level functions: `to_dataframes()`, `to_dataframe()`, `list_symbols()`, `get_data_types()`, `to_gdx()` — exposed at package top level.
- Object-oriented API: `GdxFile` and `GdxSymbol` in [src/gdxpds/gdx.py](src/gdxpds/gdx.py) for programmatic, lazy access.

## Runtime dependency on GAMS

This package **cannot function without a GAMS installation** — there is no mock layer. The SWIG-bound GDX bindings talk to the GAMS shared library found at runtime, and are imported **lazily** (on the first GDX operation), so `import gdxpds` itself does not need a binding. Two equivalent binding sources are supported via `try/except` imports (inside the engine modules and the lazy-load helpers, not at package import):

- **Modern (recommended):** `from gams.core import gdx as gdxcc` — shipped inside `gamsapi`, which the user installs version-matched to their GAMS install (`pip install gamsapi[transfer]==xx.y.z`). Not a base dependency of gdxpds.
- **Legacy:** `import gdxcc` — the standalone PyPI package. Available via the `[legacy]` extra (`pip install gdxpds[legacy]`). Older but the SWIG C ABI is stable enough that it still works.

Other runtime notes:

- GAMS lookup order is implemented by `GamsDirFinder` in [src/gdxpds/tools.py](src/gdxpds/tools.py): `GAMS_DIR` env var → `GAMSDIR` env var → `where gams` / `which gams` → walk default install location (`C:\GAMS` on Windows; picks highest version). The Windows walk handles both the modern `C:\GAMS\<version>\` layout and the legacy `C:\GAMS\win64\<version>\` layout by looking for `gams.exe` to identify a GAMS root.
- `GAMS_DIR` remains mandatory at runtime even with pip-installed bindings, because the GDX shared library lives in the GAMS install directory, not in the wheel. The recommended pattern is one venv per GAMS install with `$Env:GAMS_DIR` pinned via `Activate.ps1` — see [dev/README.md](dev/README.md).
- **`import gdxpds` works with no binding installed.** The `gdxcc.GMS_*` type codes are hardcoded in the `GamsDataType`/`GamsVariableType`/`GamsEquationType`/`GamsValueType` enums (with `tests/test_imports.py::test_gms_constants_match_gdxcc` verifying them against the live binding when present), and the bindings load on the first GDX op. So `gdxpds info` / `gdxpds test` can diagnose the "no bindings installed" environment.

If tests fail with "cannot load gdxcc" or "no `_gdxcc` module," it's a GAMS environment problem (missing `GAMS_DIR`, missing bindings, or version skew between `gamsapi` and the GAMS install), not a code bug.

## Common commands

PowerShell on Windows. Always activate the venv first:

```powershell
.venv\Scripts\Activate.ps1
```

Install for development:

```powershell
pip install -e .[test]
```

Run the full test suite:

```powershell
pytest tests
```

Run a single test file or test:

```powershell
pytest tests/test_read.py
pytest tests/test_read.py::test_name
```

Keep test output files after a run (useful when debugging round-trip failures):

```powershell
pytest tests --no-clean-up
```

The custom `--no-clean-up` flag and the shared test fixtures (`base_dir`, `run_dir`, `manage_rundir`, `roundtrip_one_gdx`) are defined in [tests/conftest.py](tests/conftest.py).

The installed `gdxpds` CLI exposes three subcommands:

```powershell
gdxpds --version    # terse version line
gdxpds info         # environment report (Python, bindings, GAMS_DIR + source, load status)
gdxpds test         # end-to-end install verification against the local GAMS
```

`gdxpds info` is also the Python function [gdxpds.info()](src/gdxpds/__init__.py) — it returns the report as a `str` and is contracted to never raise.

Verify a fresh install end-to-end (intended for end users; ships with the base package, no `[test]` extra needed):

```powershell
gdxpds test
```

Source lives in [src/gdxpds/cli/main.py](src/gdxpds/cli/main.py); the embedded
sample GDX is at `src/gdxpds/_verify_install/sample.gdx`, regenerable via
[dev/build_verify_install_sample.py](dev/build_verify_install_sample.py).

Build the docs (Sphinx, MyST-flavored markdown sources):

```powershell
pip install -e .[docs]   # or .[dev] for tests + docs
cd doc
.\make.bat html
```

Output is in `doc/build/html/`. Hand-authored docs are `.md` (parsed by MyST). The API page is generated automatically by `sphinx.ext.autosummary` with `:recursive:` — see [doc/source/api.md](doc/source/api.md) and the templates in [doc/source/_templates/autosummary/](doc/source/_templates/autosummary/). Full release / docs publish workflow — GitHub Actions on Release-published events — is in [dev/README.md](dev/README.md).

## Architecture notes

Things that aren't obvious from one file:

- **Lazy loading.** `GdxFile` (a `MutableSequence` of `GdxSymbol`) defaults to `lazy_load=True`. Symbol data is only pulled from the GDX file when `.dataframe` is accessed. Iterating symbol metadata is cheap; touching dataframes is not.
- **Symbol kinds drive column shape.** `GamsDataType` ([src/gdxpds/gdx.py](src/gdxpds/gdx.py)) — Set, Parameter, Variable, Equation, Alias. Variables and Equations get five value columns (Level, Marginal, Lower, Upper, Scale); Parameters and Sets get a single `Value` column. Write code in [src/gdxpds/write_gdx.py](src/gdxpds/write_gdx.py) infers the type from DataFrame shape and naming.
- **Special values.** GAMS encodes NA/EPS/+Inf/-Inf/UNDEF as fixed magic floats (e.g. 1E300, 2E300, 3E300). [src/gdxpds/special.py](src/gdxpds/special.py) converts these to/from numpy equivalents (`np.nan`, `np.inf`, and `None` for UNDEF) on read/write. Parameters get this conversion; Sets/Aliases do not (their value column is text, see below). UNDEF (`None`) is preserved on write by both engines; the gams.transfer write passes `eps_to_zero=False` so EPS survives too.
- **Set value = element text; membership = row presence.** A Set/Alias has one `Value` column holding the GAMS element text (a string; `""` = a member with no text). Every row is a member — there is no boolean. `_fixup_set_value` ([src/gdxpds/gdx.py](src/gdxpds/gdx.py)) normalizes the column to text on assignment (a `bool`/`c_bool`/missing value → `""`), so a Set can be built from dims alone, from booleans, or from text. The read path always fetches text (`gdxGetElemText()` on gdxcc; the records frame on gams.transfer); there is no `load_set_text` flag.
- **Aliases.** An Alias reads like the Set it aliases and records the parent in `GdxSymbol.aliased_with` (a parent ref, or `None`). It is written by both engines (`gdxAddAlias` / `gt.Alias`); the parent must precede it (no relaxed fallback — `DomainError` otherwise). `to_gdx(aliases={alias: parent})` and `gdxpds.gdx.append_alias()` build them; ordering follows `reorder_for_strict_domains()`, which now adds the alias→parent edge. The parent is typically a Set, but an Alias is accepted too: GDX supports chained aliases, and the gdxcc engine preserves the chain on disk (`aat -> at -> t`) while gams.transfer flattens it to the root (`aat -> t`). On read both engines produce a same-file `aliased_with` ref. *Universe* aliases (alias of `*`) are a documented edge: they read without error (`aliased_with` resolves to `universal_set`) and round-trip within one engine, but the engines disagree on membership (gdxcc includes the `*` element, gams.transfer doesn't), so `universe_alias_fixture.gdx` is excluded from the cross-engine parity glob and covered by `tests/test_alias.py`.
- **Lazy + idempotent GAMS bind.** `load_gdxcc()` in [src/gdxpds/tools.py](src/gdxpds/tools.py) binds the GAMS library and populates `gdxpds.special` dicts on the first GDX op (called by the gdxcc engine's `__init__` before it creates a handle, and by `info()` inside try/except for diagnostics). Process state: `tools._bindings_source`, `tools._loaded_gams_dir`.
- **`gams_dir=` on the first GDX op selects the bound install.** Once loaded, subsequent `gdxCreateD(H, <dir>, ...)` calls are no-ops against the bound library regardless of `<dir>`; `load_gdxcc()` warns when a caller passes a `gams_dir` that differs from `_loaded_gams_dir`. One GAMS library per process — multi-version testing is one-venv-per-GAMS. In-process swap is feasible via `gdxLibraryUnload()` but unimplemented (design notes tracked in a GitHub issue).
- **GDX handle lifecycle** (SWIG-bound `gdxcc`; gdxcc engine only — the gams.transfer engine holds no handle). The full `new_gdxHandle_tp` → `gdxCreateD` → `gdxFree` → `delete_gdxHandle_tp` sequence lives in one place: the `_GdxHandle` RAII class in [src/gdxpds/tools.py](src/gdxpds/tools.py), used by all three create sites (`load_gdxcc` and `load_specials` via `with`; `GdxccEngine.__init__` keeps the instance). It encodes two SWIG hazards so callers don't have to:
  1. `gdxFree(H)` is **unsafe on a failed-create handle** — it dispatches through `XFree`, bound only on a successful `gdxCreateD`, so freeing after failure segfaults. `_GdxHandle` frees+deletes on success but on failure **deletes only** (the wrapper is a plain `calloc`/`free`, always safe) and never calls `gdxFree`; the create is validated by `_check_gdx_create_rc` (raises `GamsLoadError`).
  2. `gdxFree` is also **unsafe to call twice** (double `XFree` + `objectCount` underflow), so `_GdxHandle.close()` is run-once/idempotent and every `new_gdxHandle_tp()` is paired with exactly one `delete_gdxHandle_tp()`.
  The gdxcc engine owns its handle for its lifetime; `GdxFile` schedules `weakref.finalize(self, self._engine_impl.close)` — fired at the first of `cleanup()`/`__exit__`, garbage collection (it sits in a cycle via `universal_set`, so *cyclic* GC reclaims it), or interpreter exit. **No class frees from `__del__`** (which would run at teardown after module state is partially gone); the engine's `close()` binds its gdxcc callables at construction so it stays valid at shutdown. The legacy `to_dataframes`/`to_gdx` `Translator`s call `GdxFile.cleanup()` (not the removed `__del__`). Regression coverage: [tests/test_handle_lifecycle.py](tests/test_handle_lifecycle.py).

## Conventions and gotchas

- Code style & typing: **ruff** (lint + format; `[tool.ruff]` in [pyproject.toml](pyproject.toml)) and **pyright** (basic mode; `[tool.pyright]`). Run `ruff check --fix`, `ruff format`, and `pyright` before pushing, or install the local hooks with `pre-commit install` ([.pre-commit-config.yaml](.pre-commit-config.yaml)). Conventions: only the **public API** is annotated (the SWIG-bound internals stay untyped, so pyright's None-safety categories are downgraded to warnings — see `[tool.pyright]`); `E501` is delegated to the formatter; the ruff ruleset is intentionally conservative (no bugbear/docstring rules yet). The one-time bulk reformat is recorded in [.git-blame-ignore-revs](.git-blame-ignore-revs) (`git config blame.ignoreRevsFile .git-blame-ignore-revs`).
- CI — [.github/workflows/](.github/workflows/): [lint.yml](.github/workflows/lint.yml) runs ruff + pyright on PRs and `main` (GAMS-free; the only automated **code** check). The rest are docs/release: build + deploy Sphinx (PR check + main → /latest/ + per-tag /vX.Y.Z/) and publish to PyPI on Release. **Tests still run locally** before release, per [dev/README.md](dev/README.md); there is no test CI.
- Test fixtures include real `.gdx` and `.csv` files in [tests/](tests/). These live outside the package and are **not** shipped in the wheel — `pytest tests` runs against a clone of the repo. Don't delete them.
- The two CLI scripts live in [src/gdxpds/cli/](src/gdxpds/cli/) and are installed via `[project.scripts]` in [pyproject.toml](pyproject.toml) as `csv_to_gdx` and `gdx_to_csv`. Tests subprocess these names directly (e.g. `subprocess.run(["csv_to_gdx", ...], check=True)`), which exercises the installed entry points and keeps each round-trip in its own process.
- Laboratory rename: this project moved from NREL to NLR in late 2025. New docs/copyright should say NLR / Alliance for Energy Innovation; historical attributions stay as NREL. The repo's GitHub org is `NatLabRockies`.
