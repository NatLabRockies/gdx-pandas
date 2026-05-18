# Developer How-To

To get all of the development dependencies for Python:

```
pip install -e .[admin]
```

Also, you will need to install

- [pandoc](https://pandoc.org/installing.html)

## Maintain multiple .venvs for testing

Because `gdxpds` depends on the GAMS shared libraries, validating a change typically means exercising the package against more than one GAMS install. The recommended pattern is one Python virtual environment per GAMS install you care about, each pinning its own `GAMS_DIR` so activating the venv automatically points at the intended GAMS.

For example, on Windows with PowerShell:

```powershell
py -3.11 -m venv .venv-old   # pinned to an older GAMS
py -3.13 -m venv .venv-new   # pinned to your newest GAMS
```

To make a venv self-pin its `GAMS_DIR`, edit its `Scripts\Activate.ps1` (two paste-in additions, following the same `_OLD_VIRTUAL_*` sentinel convention the script already uses for `PYTHONHOME` and `PATH`):

1. **Inside the `deactivate` function**, right after the existing `PATH` restore block, add:

    ```powershell
    # The prior GAMS_DIR:
    if (Test-Path -Path Env:_OLD_VIRTUAL_GAMS_DIR) {
        Copy-Item -Path Env:_OLD_VIRTUAL_GAMS_DIR -Destination Env:GAMS_DIR
        Remove-Item -Path Env:_OLD_VIRTUAL_GAMS_DIR
    }
    elseif (Test-Path -Path Env:GAMS_DIR) {
        Remove-Item -Path Env:GAMS_DIR
    }
    ```

2. **At the very bottom of the script**, append (substituting the right path for this venv):

    ```powershell
    # Pin GAMS_DIR for this venv
    if (Test-Path -Path Env:GAMS_DIR) {
        Copy-Item -Path Env:GAMS_DIR -Destination Env:_OLD_VIRTUAL_GAMS_DIR
    }
    $Env:GAMS_DIR = "C:\GAMS\48"
    ```

Verify with `echo $env:GAMS_DIR` right after `Activate.ps1` runs, and confirm it goes away after `deactivate`.

**Caveat:** `Activate.ps1` is regenerated whenever the venv is recreated (`python -m venv .venv-old` overwrites it), so these edits are lost on recreation. Re-apply them, or keep a copy of the customized script next to the project for easy restoration.

For a typical compatibility check, install the GAMS-version-matched `gamsapi` and gdxpds in each venv:

```powershell
.\.venv-old\Scripts\Activate.ps1
pip install gamsapi[transfer]==<old GAMS version>
pip install -e .[test]
pytest gdxpds\test

deactivate
.\.venv-new\Scripts\Activate.ps1
pip install gamsapi[transfer]==<new GAMS version>
pip install -e .[test]
pytest gdxpds\test
```

## Create a new release

1. Update version number (`gdxpds/__init__.py`), CHANGES.txt, `pyproject.toml`, LICENSE and header as needed
2. Run tests locally and fix any issues
3. Install from github and make sure tests pass 
4. Uninstall the draft package
5. Publish documentation
6. Create release on github
7. Release tagged version on pypi
    
## Publish documentation

The documentation is built with [Sphinx](http://sphinx-doc.org/index.html). There are several steps to creating and publishing the documentation:

1. Convert .md input files to .rst
2. Refresh API documentation
3. Build the HTML docs
4. Push to GitHub

### Markdown to reStructuredText

Markdown files are registered in `doc/source/md_files.txt`. Paths in that file should be relative to the docs folder and should exclude the file extension. For every file listed there, the `dev/md_to_rst.py` utility will expect to find a markdown (`.md`) file, and will look for an optional `.postfix` file, which is expected to contain `.rst` code to be appended to the `.rst` file created by converting the input `.md` file. Thus, running `dev/md_to_rst.py` on the `doc/source/md_files.txt` file will create revised `.rst` files, one for each entry listed in the registry. In summary:

```
cd doc/source
python ../../dev/md_to_rst.py md_files.txt
```

### Refresh API Documentation

- Make sure gdx-pandas is in your PYTHONPATH
- Delete the contents of `source/api`.
- Run `sphinx-apidoc -o source/api ..` from the `doc` folder.
- Compare `source/api/modules.rst` to `source/api.rst`. Delete `setup.rst` and references to it.
- Copy-paste the text in `gdxpds.postfix` at the bottom of `gdxpds.rst`
- 'git push' changes to the documentation source code as needed.
- Make the documentation per below

### Building HTML Docs

Run `make html` for Mac and Linux; `make.bat html` for Windows.

### Pushing to GitHub Pages

#### Mac/Linux

```
make github
```

#### Windows

```
make.bat html
```

Then run the github-related commands by hand:

```
git branch -D gh-pages
git push origin --delete gh-pages
ghp-import -n -b gh-pages -m "Update documentation" ./build/html
git checkout gh-pages
git push origin gh-pages
git checkout main # or whatever branch you were on
```

## Release on pypi

1. [using testpyi](https://packaging.python.org/guides/using-testpypi/) has good instructions for setting up your user account on TestPyPI and PyPI, and configuring twine to know how to access both repositories.
2. Test the package

    ```
    python -m build
    twine upload --repository testpypi dist/*
    # look at https://test.pypi.org/project/gdxpds/
    pip install --index-url https://test.pypi.org/simple/ gdxpds
    # check it out ... fix things ...
    ```

3. Upload to pypi

    ```
    twine upload --repository pypi dist/*
    ```
