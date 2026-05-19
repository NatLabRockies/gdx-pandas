# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package does

`gdxpds` translates between GDX (GAMS Data eXchange) files and pandas DataFrames. GDX is the binary file format used by [GAMS](https://www.gams.com/), a mathematical optimization modeling system. Two entry points:

- High-level functions: `to_dataframes()`, `to_dataframe()`, `list_symbols()`, `get_data_types()`, `to_gdx()` — exposed at package top level.
- Backend classes: `GdxFile` and `GdxSymbol` in [src/gdxpds/gdx.py](src/gdxpds/gdx.py) for programmatic, lazy access.

## Runtime dependency on GAMS

This package **cannot function without a GAMS installation** — there is no mock layer. The SWIG-bound GDX bindings are imported at module load and talk to the GAMS shared library found at runtime. Two equivalent binding sources are supported via `try/except` imports in [src/gdxpds/__init__.py](src/gdxpds/__init__.py), [src/gdxpds/special.py](src/gdxpds/special.py), and [src/gdxpds/gdx.py](src/gdxpds/gdx.py):

- **Modern (recommended):** `from gams.core import gdx as gdxcc` — shipped inside `gamsapi`, which the user installs version-matched to their GAMS install (`pip install gamsapi[transfer]==xx.y.z`). Not a base dependency of gdxpds.
- **Legacy:** `import gdxcc` — the standalone PyPI package. Available via the `[legacy]` extra (`pip install gdxpds[legacy]`). Older but the SWIG C ABI is stable enough that it still works.

Other runtime notes:

- GAMS lookup order is implemented by `GamsDirFinder` in [src/gdxpds/tools.py](src/gdxpds/tools.py): `GAMS_DIR` env var → `GAMSDIR` env var → `where gams` / `which gams` → walk default install location (`C:\GAMS` on Windows; picks highest version). The Windows walk handles both the modern `C:\GAMS\<version>\` layout and the legacy `C:\GAMS\win64\<version>\` layout by looking for `gams.exe` to identify a GAMS root.
- `GAMS_DIR` remains mandatory at runtime even with pip-installed bindings, because the GDX shared library lives in the GAMS install directory, not in the wheel. The recommended pattern is one venv per GAMS install with `$Env:GAMS_DIR` pinned via `Activate.ps1` — see [dev/README.md](dev/README.md).
- **Known issue: `import gdxpds` requires at least one binding installed.** [src/gdxpds/gdx.py](src/gdxpds/gdx.py) and [src/gdxpds/special.py](src/gdxpds/special.py) top-level-import `gdxcc` (modern or legacy); if neither is installed, `import gdxpds` raises `ModuleNotFoundError`. This means `gdxpds info` / `gdxpds test` cannot diagnose the exact "no bindings installed" environment they would be most useful for. Fix would defer the `gdxcc` imports (and the `gdxcc.GMS_*` constants currently referenced by the `GamsDataType` enum at module load).

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
- **Special values.** GAMS encodes NA/EPS/+Inf/-Inf/UNDEF as fixed magic floats (e.g. 1E300, 2E300, 3E300). [src/gdxpds/special.py](src/gdxpds/special.py) converts these to/from numpy equivalents (`np.nan`, `np.inf`) on read/write. Parameters and Sets bypass this conversion — keep that in mind when debugging value mismatches.
- **Set text vs. set membership.** By default, Set values are booleans (membership). Pass `load_set_text=True` to surface the GAMS element text via `gdxGetElemText()`. The `_fixup_set_vals` flag controls boolean coercion on write.
- **Optional fast path.** If `gdx2py` is importable (Windows-only), it is used to read Parameters faster than streaming through the SWIG bindings. The slow path through `gdxDataReadStr()` is the default.
- **Lazy + idempotent GAMS bind.** `load_gdxcc()` in [src/gdxpds/tools.py](src/gdxpds/tools.py) binds the GAMS library and populates `gdxpds.special` dicts on the first GDX op (called by `GdxFile._create_gdx_object` before each handle, and by `info()` inside try/except for diagnostics). Process state: `tools._bindings_source`, `tools._loaded_gams_dir`.
- **`gams_dir=` on the first GDX op selects the bound install.** Once loaded, subsequent `gdxCreateD(H, <dir>, ...)` calls are no-ops against the bound library regardless of `<dir>`; `load_gdxcc()` warns when a caller passes a `gams_dir` that differs from `_loaded_gams_dir`. One GAMS library per process — multi-version testing is one-venv-per-GAMS. In-process swap is feasible via `gdxLibraryUnload()` but unimplemented; design notes in [dev/RUNTIME_GAMS_SWAP.md](dev/RUNTIME_GAMS_SWAP.md).
- **GDX handle lifecycle quirks** (SWIG-bound `gdxcc`):
  1. `gdxFree(H)` is **unsafe on a failed-create handle** — it dispatches through `XFree`, a function pointer bound only on successful `gdxCreateD`, so freeing after failure segfaults. Only call `gdxFree` on success paths (`GdxFile.cleanup`, `load_specials`, `load_gdxcc` happy path); `_check_gdx_create_rc` in [src/gdxpds/tools.py](src/gdxpds/tools.py) deliberately does not free.
  2. `new_gdxHandle_tp(H)` is never paired with `delete_gdxHandle_tp(H)` anywhere ([src/gdxpds/gdx.py](src/gdxpds/gdx.py), [src/gdxpds/special.py](src/gdxpds/special.py), [src/gdxpds/tools.py](src/gdxpds/tools.py)), leaking the SWIG pointer wrapper. Small fixed-size leak per call site; most sites run once per process. Fixing it cleanly is a separate pass that also touches `GdxFile`'s `atexit` / `__del__` / `cleanup` chain.

## Conventions and gotchas

- No linter, formatter, or type-checker is configured. Don't introduce one unprompted.
- CI is docs-only — [.github/workflows/](.github/workflows/) builds and deploys Sphinx (PR check + main → /latest/ + per-tag /vX.Y.Z/) and publishes the package to PyPI on Release. Tests still run **locally** before release, per [dev/README.md](dev/README.md); there is no test CI.
- Test fixtures include real `.gdx` and `.csv` files in [tests/](tests/). These live outside the package and are **not** shipped in the wheel — `pytest tests` runs against a clone of the repo. Don't delete them.
- The two CLI scripts live in [src/gdxpds/cli/](src/gdxpds/cli/) and are installed via `[project.scripts]` in [pyproject.toml](pyproject.toml) as `csv_to_gdx` and `gdx_to_csv`. Tests subprocess these names directly (e.g. `subprocess.run(["csv_to_gdx", ...], check=True)`), which exercises the installed entry points and keeps each round-trip in its own process.
- The old `.py`-suffixed commands (`csv_to_gdx.py`, `gdx_to_csv.py`) are preserved for one release via thin wrapper scripts in [bin/](bin/), installed by the deprecated setuptools `script-files` mechanism. They emit a `DeprecationWarning` when invoked. **Cleanup in the next (interface-breaking) release:** delete [bin/](bin/), drop the `[tool.setuptools] script-files = [...]` block from [pyproject.toml](pyproject.toml), and remove the `main_py_alias()` functions from `src/gdxpds/cli/csv_to_gdx.py` and `src/gdxpds/cli/gdx_to_csv.py`.
- Laboratory rename: this project moved from NREL to NLR in late 2025. New docs/copyright should say NLR / Alliance for Energy Innovation; historical attributions stay as NREL. The repo's GitHub org is `NatLabRockies`.
