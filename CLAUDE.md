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
- On Linux, `gdxpds` **must be imported before pandas** to avoid a shared-library conflict; the package warns at import time if pandas is already loaded. See [src/gdxpds/__init__.py](src/gdxpds/__init__.py).

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

Build the docs (Sphinx):

```powershell
cd doc
.\make.bat html
```

Full release / docs publish workflow is in [dev/README.md](dev/README.md).

## Architecture notes

Things that aren't obvious from one file:

- **Lazy loading.** `GdxFile` (a `MutableSequence` of `GdxSymbol`) defaults to `lazy_load=True`. Symbol data is only pulled from the GDX file when `.dataframe` is accessed. Iterating symbol metadata is cheap; touching dataframes is not.
- **Symbol kinds drive column shape.** `GamsDataType` ([src/gdxpds/gdx.py](src/gdxpds/gdx.py)) — Set, Parameter, Variable, Equation, Alias. Variables and Equations get five value columns (Level, Marginal, Lower, Upper, Scale); Parameters and Sets get a single `Value` column. Write code in [src/gdxpds/write_gdx.py](src/gdxpds/write_gdx.py) infers the type from DataFrame shape and naming.
- **Special values.** GAMS encodes NA/EPS/+Inf/-Inf/UNDEF as fixed magic floats (e.g. 1E300, 2E300, 3E300). [src/gdxpds/special.py](src/gdxpds/special.py) converts these to/from numpy equivalents (`np.nan`, `np.inf`) on read/write. Parameters and Sets bypass this conversion — keep that in mind when debugging value mismatches.
- **Set text vs. set membership.** By default, Set values are booleans (membership). Pass `load_set_text=True` to surface the GAMS element text via `gdxGetElemText()`. The `_fixup_set_vals` flag controls boolean coercion on write.
- **Optional fast path.** If `gdx2py` is importable (Windows-only), it is used to read Parameters faster than streaming through the SWIG bindings. The slow path through `gdxDataReadStr()` is the default.
- **`gams_dir=` kwargs are path/config, not library-swap.** `load_gdxcc()` runs once at `import gdxpds` time and binds the GAMS shared library into the process. After that first successful load, subsequent `gdxCreateD(H, <dir>, ...)` calls treat the directory argument as a no-op — they return success and hand back a working handle that operates on the *already-loaded* library, regardless of what `<dir>` says (empirically verified: post-import, `gdxCreateD(H, "C:\\Windows", ...)` returns rc=1 and the handle reads GDX files just fine). That means the `gams_dir=` parameter on `GdxFile`, `to_dataframes`, `to_gdx`, `gdxpds.info()`, the `-g`/`--gams_dir` CLI flag, etc. — all useful for *path validation* and *configuration intent*, but they cannot swap GAMS at runtime *in current gdxpds*. The only mechanism that actually selects which GAMS gets loaded is the env-var-driven bootstrap (`GAMS_DIR` / `GAMSDIR`, per-venv-pinned via `Activate.ps1` — see [dev/README.md](dev/README.md)). Multi-GAMS testing is one-venv-per-GAMS, not one-process-per-GAMS. (The C bindings *do* expose primitives that would make in-process swap feasible — see "Runtime GAMS swap" below.)

## Runtime GAMS swap (feasible; not implemented)

The "one-process-per-GAMS" constraint above is a property of `gdxpds`, not of the underlying C bindings. `gams.core.gdx` exposes two primitives that together would allow swapping GAMS at runtime:

- `gdxLibraryLoaded() -> int` — returns 1 if the GDX shared library is currently bound to the process, 0 otherwise.
- `gdxLibraryUnload() -> int` — unloads the bound library; returns 1 on success.

Verified behavior on Windows with `gamsapi 48.7.0`:

1. Before any `import gdxpds`: `gdxLibraryLoaded() == 0`.
2. After `import gdxpds`: `gdxLibraryLoaded() == 1`.
3. After `gdxLibraryUnload()`: returns 1, and `gdxLibraryLoaded() == 0`.
4. A subsequent `gdxCreateD(H, "/different/GAMS", ...)` re-loads from the new directory: rc=1, `gdxLibraryLoaded() == 1`.

So an in-process GAMS swap is technically reachable. The recipe would be: close all open handles → `gdxLibraryUnload()` → fresh `gdxCreateD(H, new_dir, ...)`.

**Caveats that would need verification before relying on this:**

1. **Stale handles.** Any `GdxFile` / raw `H` handles created against the previous library reference unloaded memory after unload. Operations on them likely segfault. A real implementation needs to find and close them all, or refuse to unload while any are open.
2. **Cold-start crash risk re-emerges.** The first `gdxCreateD` after unload faces the same "non-GAMS dir → access violation on Windows" failure mode as a cold-start. `_require_gams_installation` in [src/gdxpds/tools.py](src/gdxpds/tools.py) would need to run before any reload, not just at import.
3. **`gdxpds.special` state.** [src/gdxpds/special.py](src/gdxpds/special.py) populates `SPECIAL_VALUES`, `GDX_TO_NP_SVS`, `NP_TO_GDX_SVS` module-level dicts from the loaded library. These are GDX-format constants and likely identical across GAMS versions, but a reload-aware API should re-call `load_specials()` for hygiene.
4. **`load_gdxcc()` is not reload-aware.** It just calls `gdxCreateD` again, which (as documented above) treats the dir arg as a no-op once the library is loaded. A reload entry point would need to call `gdxLibraryUnload()` first.
5. **Linux semantics.** All verification above is Windows-only. `dlclose()` on Linux is famously unreliable for "really fully unload" — the kernel may keep the library mapped if any references remain. Cross-platform behavior should be checked during the Linux multi-version testing pass.

A shape for a future `gdxpds.reload_gdxcc(gams_dir)` would: assert no live handles → run pre-checks (`_require_gams_installation`) → call `gdxLibraryUnload()` → fresh `gdxCreateD(H, gams_dir, ...)` → re-call `load_specials()`. The Linux pass is the right time to design this — that's where it'd be most useful (multi-version testing) and where the platform edge cases would surface.

## Conventions and gotchas

- No linter, formatter, or type-checker is configured. Don't introduce one unprompted.
- No CI workflow in this repo — tests are run locally before release.
- Test fixtures include real `.gdx` and `.csv` files in [tests/](tests/). These live outside the package and are **not** shipped in the wheel — `pytest tests` runs against a clone of the repo. Don't delete them.
- The two CLI scripts live in [src/gdxpds/cli/](src/gdxpds/cli/) and are installed via `[project.scripts]` in [pyproject.toml](pyproject.toml) as `csv_to_gdx` and `gdx_to_csv`. Tests subprocess these names directly (e.g. `subprocess.run(["csv_to_gdx", ...], check=True)`), which exercises the installed entry points and preserves process isolation — important for the load-bearing Linux `import gdxpds` before `import pandas` ordering.
- The old `.py`-suffixed commands (`csv_to_gdx.py`, `gdx_to_csv.py`) are preserved for one release via thin wrapper scripts in [bin/](bin/), installed by the deprecated setuptools `script-files` mechanism. They emit a `DeprecationWarning` when invoked. **Cleanup in the next (interface-breaking) release:** delete [bin/](bin/), drop the `[tool.setuptools] script-files = [...]` block from [pyproject.toml](pyproject.toml), and remove the `main_py_alias()` functions from `src/gdxpds/cli/csv_to_gdx.py` and `src/gdxpds/cli/gdx_to_csv.py`.
- Laboratory rename: this project moved from NREL to NLR in late 2025. New docs/copyright should say NLR / Alliance for Energy Innovation; historical attributions stay as NREL. The repo's GitHub org is `NatLabRockies`.
