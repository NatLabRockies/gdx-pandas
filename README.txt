gdx-pandas: Python package to translate between gdx (GAMS data) and pandas. 

There are two main ways to use gdxpds. The first use case is the one that was 
initially supported: direct conversion between GDX files on disk and pandas 
DataFrames or a csv version thereof. Starting with the Version 1.0.0 rewrite,
there is now a second style of use which involves interfacing with GDX files and 
symbols via the `gdxpds.gdx.GdxFile` and `gdxpds.gdx.GdxSymbol` classes.

Please visit https://NatLabRockies.github.io/gdx-pandas for the latest documentation.


DEPENDENCIES

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


TESTING

After installation, you can test the package using pytest:

pytest --pyargs gdxpds

If the tests fail due to permission IOErrors, apply `chmod g+x` and `chmod a+x` 
to the `gdx-pandas/gdxpds/test` folder.
