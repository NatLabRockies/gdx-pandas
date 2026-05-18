# gdx-pandas
[![PyPI](https://img.shields.io/pypi/v/gdxpds.svg)](https://pypi.python.org/pypi/gdxpds/)
[![Documentation](https://img.shields.io/badge/docs-ready-blue.svg)](https://NatLabRockies.github.io/gdx-pandas)

gdx-pandas is a python package to translate between gdx (GAMS data) and pandas. 

[Install](#install) | [Documentation](https://NatLabRockies.github.io/gdx-pandas) | [Uninstall](#uninstall)

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
