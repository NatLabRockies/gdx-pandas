# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this package does

`gdxpds` translates between GDX (GAMS Data eXchange) files and pandas DataFrames. GDX is the binary file format used by [GAMS](https://www.gams.com/), a mathematical optimization modeling system. Two entry points:

- High-level functions: `to_dataframes()`, `to_dataframe()`, `list_symbols()`, `get_data_types()`, `to_gdx()` — exposed at package top level.
- Backend classes: `GdxFile` and `GdxSymbol` in [gdxpds/gdx.py](gdxpds/gdx.py) for programmatic, lazy access.

## Runtime dependency on GAMS

This package **cannot function without a GAMS installation** — there is no mock layer. The SWIG-bound GDX bindings are imported at module load and talk to the GAMS shared library found at runtime. Two equivalent binding sources are supported via `try/except` imports in [gdxpds/__init__.py](gdxpds/__init__.py), [gdxpds/special.py](gdxpds/special.py), and [gdxpds/gdx.py](gdxpds/gdx.py):

- **Modern (recommended):** `from gams.core import gdx as gdxcc` — shipped inside `gamsapi`, which the user installs version-matched to their GAMS install (`pip install gamsapi[transfer]==xx.y.z`). Not a base dependency of gdxpds.
- **Legacy:** `import gdxcc` — the standalone PyPI package. Available via the `[legacy]` extra (`pip install gdxpds[legacy]`). Older but the SWIG C ABI is stable enough that it still works.

Other runtime notes:

- GAMS lookup order is implemented by `GamsDirFinder` in [gdxpds/tools.py](gdxpds/tools.py): `GAMS_DIR` env var → `GAMSDIR` env var → `where gams` / `which gams` → walk default install location (`C:\GAMS` on Windows; picks highest version). The Windows walk handles both the modern `C:\GAMS\<version>\` layout and the legacy `C:\GAMS\win64\<version>\` layout by looking for `gams.exe` to identify a GAMS root.
- `GAMS_DIR` remains mandatory at runtime even with pip-installed bindings, because the GDX shared library lives in the GAMS install directory, not in the wheel. The recommended pattern is one venv per GAMS install with `$Env:GAMS_DIR` pinned via `Activate.ps1` — see [dev/README.md](dev/README.md).
- On Linux, `gdxpds` **must be imported before pandas** to avoid a shared-library conflict; the package warns at import time if pandas is already loaded. See [gdxpds/__init__.py](gdxpds/__init__.py).

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

Run the full test suite (works on installed copy via `--pyargs`, or against the local source tree):

```powershell
pytest --pyargs gdxpds
pytest gdxpds/test
```

Run a single test file or test:

```powershell
pytest gdxpds/test/test_read.py
pytest gdxpds/test/test_read.py::test_name
```

Keep test output files after a run (useful when debugging round-trip failures):

```powershell
pytest gdxpds/test --no-clean-up
```

The custom `--no-clean-up` flag is registered in [gdxpds/test/conftest.py](gdxpds/test/conftest.py) and consumed by per-test cleanup fixtures.

Build the docs (Sphinx):

```powershell
cd doc
.\make.bat html
```

Full release / docs publish workflow is in [dev/README.md](dev/README.md).

## Architecture notes

Things that aren't obvious from one file:

- **Lazy loading.** `GdxFile` (a `MutableSequence` of `GdxSymbol`) defaults to `lazy_load=True`. Symbol data is only pulled from the GDX file when `.dataframe` is accessed. Iterating symbol metadata is cheap; touching dataframes is not.
- **Symbol kinds drive column shape.** `GamsDataType` ([gdxpds/gdx.py](gdxpds/gdx.py)) — Set, Parameter, Variable, Equation, Alias. Variables and Equations get five value columns (Level, Marginal, Lower, Upper, Scale); Parameters and Sets get a single `Value` column. Write code in [gdxpds/write_gdx.py](gdxpds/write_gdx.py) infers the type from DataFrame shape and naming.
- **Special values.** GAMS encodes NA/EPS/+Inf/-Inf/UNDEF as fixed magic floats (e.g. 1E300, 2E300, 3E300). [gdxpds/special.py](gdxpds/special.py) converts these to/from numpy equivalents (`np.nan`, `np.inf`) on read/write. Parameters and Sets bypass this conversion — keep that in mind when debugging value mismatches.
- **Set text vs. set membership.** By default, Set values are booleans (membership). Pass `load_set_text=True` to surface the GAMS element text via `gdxGetElemText()`. The `_fixup_set_vals` flag controls boolean coercion on write.
- **Optional fast path.** If `gdx2py` is importable (Windows-only), it is used to read Parameters faster than streaming through the SWIG bindings. The slow path through `gdxDataReadStr()` is the default.

## Conventions and gotchas

- No linter, formatter, or type-checker is configured. Don't introduce one unprompted.
- No CI workflow in this repo — tests are run locally before release.
- Test fixtures include real `.gdx` and `.csv` files in [gdxpds/test/](gdxpds/test/); these are packaged via `[tool.setuptools.package-data]` in [pyproject.toml](pyproject.toml). Don't delete them.
- The two CLI scripts in [bin/](bin/) (`csv_to_gdx.py`, `gdx_to_csv.py`) are installed via `[tool.setuptools.script-files]` in [pyproject.toml](pyproject.toml) — keep them runnable as `__main__`. Test code that spawns them as subprocesses must use `sys.executable`, not bare `'python'`, so the child inherits the same interpreter pytest is running on.
- Laboratory rename: this project moved from NREL to NLR in late 2025. New docs/copyright should say NLR / Alliance for Energy Innovation; historical attributions stay as NREL. The repo's GitHub org is `NatLabRockies`.
