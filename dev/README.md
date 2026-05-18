# Developer How-To

To get all of the development dependencies for Python:

```
pip install -e .[admin]
```

Also, you will need to install

- [pandoc](https://pandoc.org/installing.html)

## Maintain multiple .venvs for testing

Because `gdxpds` depends on the GAMS shared libraries, validating a change typically means exercising the package against more than one GAMS install. The recommended pattern is one Python virtual environment per GAMS install you care about, each pinning its own `GAMS_DIR` so activating the venv automatically points at the intended GAMS.

### On Windows (PowerShell)

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

Verify with `gdxpds info` right after `Activate.ps1` runs — the report will show `GAMS_DIR: <pinned path>` and `source: GAMS_DIR env var`. Confirm with `echo $env:GAMS_DIR` that the variable goes away after `deactivate`.

**Caveat:** `Activate.ps1` is regenerated whenever the venv is recreated (`python -m venv .venv-old` overwrites it), so these edits are lost on recreation. Re-apply them, or keep a copy of the customized script next to the project for easy restoration.

For a typical compatibility check, install the GAMS-version-matched `gamsapi` and gdxpds in each venv:

```powershell
.\.venv-old\Scripts\Activate.ps1
pip install gamsapi[transfer]==<old GAMS version>
pip install -e .[test]
pytest tests

deactivate
.\.venv-new\Scripts\Activate.ps1
pip install gamsapi[transfer]==<new GAMS version>
pip install -e .[test]
pytest tests
```

### On Linux with `environment-modules`

On HPC-style hosts where multiple GAMS versions are exposed via `module load gams/<ver>`, the same multi-venv pattern works in bash. The `bin/activate` script gets the equivalent of the Windows patch: `module load` on activate, `module unload` + restore the prior `GAMS_DIR` on deactivate.

A representative matrix (three GAMS-present venvs plus one no-GAMS venv for negative testing):

| venv | module | what to `pip install` after activate |
|---|---|---|
| `.venv-gams-34` | `gams/34.3.0` | `pip install -e '.[test,legacy]'` (pulls `gdxcc` for the legacy SWIG bindings; pre-`gamsapi` era) |
| `.venv-gams-49` | `gams/49.6.0` | `pip install -e '.[test]'` then `pip install 'gamsapi[transfer]==49.6.0'` |
| `.venv-gams-51` | `gams/51.3.0` | `pip install -e '.[test]'` then `pip install 'gamsapi[transfer]==51.3.0'` |
| `.venv-no-gams` | — (do not load) | `pip install -e .` — exercises `gdxpds test` failure paths |

Create them all up front:

```bash
python -m venv .venv-gams-34
python -m venv .venv-gams-49
python -m venv .venv-gams-51
python -m venv .venv-no-gams
```

**Activate-script patch (bash).** For each of the three `*-gams-*` venvs, edit its `bin/activate` in two places (mirroring the Windows pattern):

1. **Inside the existing `deactivate ()` function**, right after the `PATH` restore block, add:

    ```bash
    # Restore previous GAMS_DIR / unload module
    if [ -n "${_OLD_VIRTUAL_GAMS_MODULE_LOADED:-}" ] ; then
        module unload gams 2>/dev/null || true
        unset _OLD_VIRTUAL_GAMS_MODULE_LOADED
    fi
    if [ -n "${_OLD_VIRTUAL_GAMS_DIR:-}" ] ; then
        export GAMS_DIR="$_OLD_VIRTUAL_GAMS_DIR"
    else
        unset GAMS_DIR
    fi
    unset _OLD_VIRTUAL_GAMS_DIR
    ```

2. **Right before the trailing `hash -r` block** (the bash/zsh-specific block guarded by `if [ -n "${BASH:-}" -o -n "${ZSH_VERSION:-}" ]`, near the bottom of the file), insert — substituting the right version per venv:

    ```bash
    # Pin GAMS for this venv
    _OLD_VIRTUAL_GAMS_DIR="${GAMS_DIR:-}"
    _OLD_VIRTUAL_GAMS_MODULE_LOADED=""
    if command -v module >/dev/null 2>&1 ; then
        module load gams/51.3.0   # <-- adjust per venv
        _OLD_VIRTUAL_GAMS_MODULE_LOADED=1
    fi
    if command -v gams >/dev/null 2>&1 ; then
        export GAMS_DIR="$(dirname "$(command -v gams)")"
    fi
    ```

    Placement matters: the block must run *after* PATH is set so `module load` and `command -v gams` see the venv's PATH, and *before* `hash -r` so bash's command-cache refresh picks up the freshly module-loaded `gams`.

For `.venv-no-gams`, use the same deactivate block but replace the pin-GAMS block above with one that *clears* GAMS instead of loading it (defensive, in case the parent shell already has GAMS in the environment):

```bash
# Force no-GAMS environment for this venv
_OLD_VIRTUAL_GAMS_DIR="${GAMS_DIR:-}"
_OLD_VIRTUAL_GAMS_MODULE_LOADED=""
if command -v module >/dev/null 2>&1 ; then
    module unload gams 2>/dev/null || true
fi
unset GAMS_DIR
```

Verify with `echo "$GAMS_DIR"; command -v gams` right after `source bin/activate`, and confirm both go away (or change back) after `deactivate`.

**Caveat (same as Windows):** `bin/activate` is regenerated whenever the venv is recreated (`python -m venv .venv-gams-XX` overwrites it), so these edits are lost on recreation. Re-apply them, or keep a copy of the customized scripts next to the project for easy restoration.

**Note on `module`.** `module` is a shell function set up by `/etc/profile.d/modules.sh` and is normally available only in interactive shells. The patch above guards `module` calls with `command -v module` so the venv still works on a developer laptop without environment-modules; in that case you'd set `GAMS_DIR` yourself or rely on `which gams`.

#### Run the test matrix

A helper script drives all four venvs in sequence and writes per-venv logs plus a top-level summary:

```bash
bash dev/run_test_matrix.sh
```

It runs, in each existing venv: `pytest tests`, `GDXPDS_TEST_PREIMPORT_PANDAS=1 pytest tests` (exercises the historical pandas-before-gdxpds bad-order path; see [tests/conftest.py](../tests/conftest.py)), and `gdxpds test`. For `.venv-no-gams` it expects all three commands to fail with clean exit codes (no segfaults, useful error messages).

Invoke it from an interactive bash shell so the `module` function is in scope.

## Create a new release

1. Update version number (`src/gdxpds/__init__.py`), CHANGES.txt, `pyproject.toml`, LICENSE and header as needed
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
