# Developer How-To

To get all of the development dependencies for Python:

```
pip install -e .[dev]
```

That meta-extra pulls in `[test]` (pytest) and `[docs]` (sphinx, sphinx_rtd_theme, myst-parser). Use `.[test]` or `.[docs]` if you only want one. Build/release tooling (`build`, `twine`, etc.) is no longer a local concern — see [Releases](#create-a-new-release) below.

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

## Create a new release

Two GitHub Actions workflows make a release fully automatic from the Releases UI: [release-pypi.yml](../.github/workflows/release-pypi.yml) publishes to PyPI via Trusted Publishing (OIDC, no API token stored anywhere), and [release-docs.yml](../.github/workflows/release-docs.yml) builds docs against the release tag and deploys them under `https://NatLabRockies.github.io/gdx-pandas/vX.Y.Z/`. Both gate on `release.prerelease == false`, so pre-release tags (e.g. `v2.0.0rc1`) are no-ops for automation — if you ever need a pre-release on PyPI, run `python -m build` and `twine upload` by hand.

The end-to-end flow:

1. Update version number in `src/gdxpds/__init__.py`, `CHANGES.txt`, `pyproject.toml` (if hardcoded anywhere), and `LICENSE` header as needed. Commit and merge to `main`.
2. Run `pytest tests` locally against each GAMS-pinned venv (see [Maintain multiple .venvs for testing](#maintain-multiple-venvs-for-testing)).
3. On GitHub: Releases → **Draft a new release** → tag `vX.Y.Z` (matching `gdxpds.__version__` — `release-pypi.yml` enforces this) → write release notes → **Publish release**.
4. Within ~5 minutes both workflows complete:
    - `pip install gdxpds==X.Y.Z` works from PyPI.
    - `https://NatLabRockies.github.io/gdx-pandas/vX.Y.Z/` is live.
    - The version dropdown on `/latest/` now offers `vX.Y.Z`.

## Documentation

Docs are built with [Sphinx](http://sphinx-doc.org/index.html) and authored in MyST-flavored markdown — see [doc/source/index.md](../doc/source/index.md), [doc/source/overview.md](../doc/source/overview.md), and [doc/source/api.md](../doc/source/api.md). The API page is generated automatically by `sphinx.ext.autosummary` (details below). Three GitHub Actions workflows manage them:

- [docs-pr.yml](../.github/workflows/docs-pr.yml) — builds Sphinx on every PR (with `-W` warnings-as-errors) and uploads the HTML as an artifact for review.
- [docs.yml](../.github/workflows/docs.yml) — on every push to `main`, rebuilds and deploys `/latest/`.
- [release-docs.yml](../.github/workflows/release-docs.yml) — on every published Release, builds against the tag and deploys `/vX.Y.Z/`.

The deployed layout on the `gh-pages` branch:

```
gh-pages/
    index.html                 # redirects to /latest/
    versions.json              # [latest, v2.0.0, v1.5.0, ...]
    latest/                    # built from main
    v1.5.0/                    # built from v1.5.0 tag
    v2.0.0/                    # built from v2.0.0 tag
    ...
```

The version dropdown (sidebar, sphinx_rtd_theme) is populated from `versions.json` at page load by [doc/source/_static/versions.js](../doc/source/_static/versions.js).

### Build docs locally

```
pip install -e .[docs]
cd doc
make.bat html      # Windows
# or: make html    # Mac/Linux
```

Output: `doc/build/html/index.html`. The version dropdown hides itself silently when there's no `versions.json` (local builds).

### API reference is fully automatic

The API page is driven by `sphinx.ext.autosummary` with `:recursive:` (see [doc/source/api.md](../doc/source/api.md)). Sphinx walks `gdxpds.*` at build time and writes per-symbol stubs into `doc/source/_autosummary/` (gitignored). Adding or removing a module under [src/gdxpds/](../src/gdxpds/) is picked up on the next build — no manual `sphinx-apidoc` step. The output style is controlled by the templates in [doc/source/_templates/autosummary/](../doc/source/_templates/autosummary/).

### Manage versioned docs from the UI

Backfill an old tag's docs:

- Actions → **Build and deploy docs** → "Run workflow" → set `version` to e.g. `v1.4.0`. The workflow checks out that tag and deploys to `/v1.4.0/`. The tag must have the MyST-based docs layout (i.e., it was released after this migration).

Delete a version's docs (e.g., dropping support for an old line you no longer want listed in the version dropdown):

- Actions → **Build and deploy docs** → "Run workflow" → set `delete_version` to e.g. `v1.4.0`. The workflow removes `gh-pages/v1.4.0/` and regenerates `versions.json` so the dropdown no longer offers it.

Both inputs are mutually exclusive in a single run.
