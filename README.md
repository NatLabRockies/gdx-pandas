# gdx-pandas
[![PyPI](https://img.shields.io/pypi/v/gdxpds.svg)](https://pypi.python.org/pypi/gdxpds/)
[![Documentation](https://img.shields.io/badge/docs-ready-blue.svg)](https://NatLabRockies.github.io/gdx-pandas)

gdx-pandas is a python package to translate between gdx (GAMS data) and pandas. 

[Install](#install) | [Documentation](https://NatLabRockies.github.io/gdx-pandas) | [Uninstall](#uninstall)

<!-- begin-install -->
## Install

### Preliminaries

- Python 3.11 or higher (exact compatibility might depend on which GAMS version you are using)
- Install [GAMS](https://www.gams.com/download/)
- Put the GAMS directory in your `PATH` and/or assign it to the `GAMS_DIR` environment variable
- GAMS Python bindings — choose one:

    **Recommended.** Install the `gamsapi` that matches your installed GAMS version:

    ```bash
    # xx.y.z corresponds to your GAMS version
    pip install gamsapi[transfer]==xx.y.z
    ```

    Installing `gamsapi` this way also enables the optional, much-faster `gams.transfer` I/O engine for large files (see [Configure](#configure) below).

    **Legacy.** Use the standalone `gdxcc` package from PyPI by installing `gdxpds` with the `legacy` extra (see below). `gdxcc` is older and is not version-matched to your GAMS install, but the SWIG-bound C ABI is stable enough that it generally works.

### Get the Latest Package

```bash
# Recommended (use with the gamsapi install above):
pip install gdxpds

# Legacy (also installs gdxcc; use if you skipped gamsapi):
pip install gdxpds[legacy]
```

Versions are listed at [pypi](https://pypi.python.org/pypi/gdxpds/) and 
https://github.com/NatLabRockies/gdx-pandas/releases.

### Configure

gdxpds needs to know **where GAMS is**, and optionally **which I/O engine** to use. Set either once via an environment variable, or per call with the `gams_dir=` / `backend=` keywords (also `--gams_dir` / `--backend` on the CLIs):

```bash
export GAMS_DIR=/path/to/gams        # otherwise auto-discovered
export GDXPDS_BACKEND=gams_transfer  # default: gdxcc; gams_transfer is much faster on large files (needs gamsapi)
```

See *Configuration* in the [documentation](https://NatLabRockies.github.io/gdx-pandas) for the full keyword / environment-variable / CLI matrix and the speed trade-offs.

## Verify installation

After installing `gdxpds` and a matching `gamsapi`, verify your environment
end-to-end with:

```bash
gdxpds test
```

For a quick environment check without running the full round-trip, use
`gdxpds info` — it prints Python, bindings, the resolved `GAMS_DIR` (and
which discovery branch produced it), and any import-time load error. Useful
for bug reports. `gdxpds --version` prints just the version.

Expected output:

```
Verifying gdxpds installation...
  [OK]   GAMS install found at <your GAMS directory>
  [OK]   GDX bindings loaded: gams.core.gdx (gamsapi)
  [OK]   Read embedded sample.gdx (...)
  [OK]   Round-trip write->read preserves all symbols
  [OK]   Special values (+Inf, -Inf, NaN) survive round-trip

PASSED: gdxpds installation verified.
```

## Development tests

To run the development test suite, clone the repo and run:

```bash
pytest tests
```

If the tests fail due to permission IOErrors, apply `chmod g+x` and `chmod a+x`
to the `tests` folder.

## Uninstall

```
pip uninstall gdxpds
```
<!-- end-install -->
