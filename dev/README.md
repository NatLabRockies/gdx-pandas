# Developer How-To

To get all of the development dependencies for Python:

```
pip install -e .[dev]
```

That meta-extra pulls in `[test]` (pytest), `[docs]` (sphinx, sphinx_rtd_theme, myst-parser), and the lint/type tooling (`ruff`, `pyright`, `pre-commit`). Use `.[test]` or `.[docs]` if you only want one. Build/release tooling (`build`, `twine`, etc.) is no longer a local concern — see [Releases](#create-a-new-release) below.

## Code style and type-checking

`ruff` handles linting and formatting; `pyright` (basic mode) type-checks the public API. Both are configured in [pyproject.toml](../pyproject.toml) (`[tool.ruff]`, `[tool.pyright]`) and installed by the `[dev]` extra.

Install the local git hooks once so commits are auto-formatted and linted (ruff only — it needs no GAMS install):

```
pre-commit install
```

Or run the checks by hand before pushing:

```
ruff check --fix
ruff format
pyright
```

CI ([lint.yml](../.github/workflows/lint.yml)) runs the same pinned ruff hooks plus pyright on every PR and on `main`; neither needs a GAMS install. Tests are not in CI — run them locally (below).

Only the **public API** is annotated; the SWIG-bound internals stay untyped, so pyright's None-safety diagnostic categories are downgraded to warnings in `[tool.pyright]` (tighten them back to errors as internals get typed). `E501` (line length) is left to the formatter rather than the linter.

The one-time bulk reformat is listed in [.git-blame-ignore-revs](../.git-blame-ignore-revs). Keep `git blame` readable past it with:

```
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

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

It runs, in each existing venv: `pytest tests` and `gdxpds test`. `.venv-no-gams` additionally runs `pip wheel --no-deps .` to confirm the wheel still builds without GAMS bindings (guards the static-attr `version` read in [pyproject.toml](../pyproject.toml)). For `.venv-no-gams`, pytest and gdxpds test should fail with clean exit codes (no segfaults, useful error messages); the wheel build should succeed.

Invoke it from an interactive bash shell so the `module` function is in scope.

## Performance benchmarks

[tests/test_engine_timing.py](../tests/test_engine_timing.py) records read/write timings + peak Python memory (via `tracemalloc`) across the gdxcc and `gams.transfer` engines, alongside raw-engine baselines (`_raw_gdxcc_write/read`, `_raw_transfer_write/read`) so the per-engine overhead is visible.

The default `pytest tests` runs the benchmarks at **500K rows** (a 5-dim synthetic Parameter, ~6 MB GDX). Results print as tables in the pytest terminal summary, grouped by `(rows, op)`.

To exercise the **5M-row scaling probe** (slow-marked, ~3 minutes against a hot GAMS install — confirms the default-scale ratios still hold an order of magnitude up, approaching the 29M-row case from [issue #65](https://github.com/NatLabRockies/gdx-pandas/issues/65)):

```
pytest tests -m slow
```

The `slow` marker is registered in [pyproject.toml](../pyproject.toml) under `[tool.pytest.ini_options]`; `addopts = "-m 'not slow'"` keeps the default suite fast.

For deeper investigation when a benchmark regresses, two `cProfile`-driven probes localize per-row hotspots in the gdxcc write loop:

```
python dev/profile_gdxcc_write.py    # gdxpds-mediated write
python dev/profile_raw_gdxcc.py      # bare gdxDataWriteStr baseline
```

Each prints a 25-entry cumulative profile of one 500K-row write, with no tracemalloc overhead distorting the per-call timing.

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
