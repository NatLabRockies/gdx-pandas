# gdx-pandas
[![PyPI](https://img.shields.io/pypi/v/gdxpds.svg)](https://pypi.python.org/pypi/gdxpds/)
[![Documentation](https://img.shields.io/badge/docs-ready-blue.svg)](https://NatLabRockies.github.io/gdx-pandas)

gdx-pandas is a python package to translate between gdx (GAMS data) and pandas. 

[Install](#install) | [Documentation](https://nrel.github.io/gdx-pandas) | [Uninstall](#uninstall)

## Install

### Preliminaries

- Python 3.7 or higher (exact compatibility might depend on which GAMS version you are using)
- pandas (In general you will want the SciPy stack. Anaconda comes with it, or see [my notes for Windows](https://elainethale.wordpress.com/programming-notes/python-environment-set-up/).)
- Install [GAMS](https://www.gams.com/download/)
- Put the GAMS directory in your `PATH` and/or assign it to the `GAMS_DIR` environment variable
- GAMS Python bindings
    - See GAMS/**/apifiles/readme.txt on Windows and Mac, or 
      /opt/gams/**/apifiles/readme.txt on Linux
    - Run the following for the correct version of the Python bindings (e.g., from the GAMS/**/apifiles/Python/api_39 folder):
        
        ```bash
        python setup.py install
        ```

        or 

        ```bash
        python setup.py build --build-base={temporary-path-where-you-have-write-access} install
        ```

        with the latter being for the case when you can install packages into 
        Python but don't have GAMS directory write access.
    - If `import gdxcc` fails (which will also cause `import gdxpds` to fail) because there "is no `_gdxcc` module", one workaround is to copy all the `_*.pyd` (or `_*.so`) files from GAMS/**/apifiles/Python/api_XX/ and paste them into your Python environment next to, e.g., the `gdxcc-8-py3.9.egg` file, which on Anaconda is your environment's lib/site-packages directory.

### Get the Latest Package

```bash
pip install gdxpds
```

or

```bash
pip install git+https://github.com/NREL/gdx-pandas.git@v1.4.0
```

or

```bash
pip install git+https://github.com/NREL/gdx-pandas.git@main
```


Versions are listed at [pypi](https://pypi.python.org/pypi/gdxpds/) and 
https://github.com/NREL/gdx-pandas/releases.

After installation, you can test the package using pytest:

```bash
pytest --pyargs gdxpds
```

If the tests fail due to permission IOErrors, apply `chmod g+x` and `chmod a+x` 
to the `gdx-pandas/gdxpds/test` folder.

## Uninstall

```
pip uninstall gdxpds
```
