# TODO — verify docs CI dependency install on Linux

**Status:** temporary; delete this file once verified.

## Context

The four GitHub Actions workflows under [../.github/workflows/](../.github/workflows/) pin `python-version: '3.13'` on `ubuntu-latest`. Three of them install docs deps via:

```bash
pip install -e .[docs,legacy]
```

`[legacy]` pulls in the `gdxcc` PyPI binding. Per the Known Issue in [../CLAUDE.md](../CLAUDE.md), `import gdxpds` requires at least one binding installed (gdxcc *or* gamsapi), so `[legacy]` is structurally necessary — that part is settled.

The only remaining unknown is whether `gdxcc` has a binary wheel available for Python 3.13 on PyPI. Wheels for SWIG-wrapped packages often lag the latest Python release. If a 3.13 wheel is missing, the CI install fails with no compiler fallback (no source dist or no toolchain on the runner).

## Test

In your Linux compute environment, **without** a real GAMS install required:

```bash
# Use a throwaway venv to avoid disturbing anything else.
python3.13 -m venv /tmp/.venv-docs-ci
source /tmp/.venv-docs-ci/bin/activate

# This is exactly what the workflows do.
cd /path/to/gdx-pandas
pip install --upgrade pip
pip install -e .[docs,legacy]

# Confirm import works (no GAMS needed).
python -c "import gdxpds; print('OK', gdxpds.__version__)"

# Confirm sphinx-build works end-to-end (this is what CI actually does).
cd doc
sphinx-build -W --keep-going -b html source build/html

deactivate
rm -rf /tmp/.venv-docs-ci
```

## Outcomes

**Pass** (everything succeeds): the workflows are correct as-is. Delete this file and commit.

**Fail at `pip install`** with a message like *"Could not find a version that satisfies the requirement gdxcc"* or *"ERROR: Failed building wheel for gdxcc"*: no Python 3.13 wheel exists. Fix by pinning workflows to a Python version that does have wheels (typically 3.12). Edit `python-version: '3.13'` → `python-version: '3.12'` in all four workflow files:

- [../.github/workflows/docs-pr.yml](../.github/workflows/docs-pr.yml)
- [../.github/workflows/docs.yml](../.github/workflows/docs.yml)
- [../.github/workflows/release-docs.yml](../.github/workflows/release-docs.yml)
- [../.github/workflows/release-pypi.yml](../.github/workflows/release-pypi.yml)

Then delete this file and commit.

**Fail at `import gdxpds`** or at `sphinx-build`: something deeper than wheel availability is wrong. Capture the traceback and we'll address it. Don't delete this file yet.

## Cleanup

After a successful verification: `git rm dev/TODO-verify-docs-ci.md` and commit alongside any python-version change you needed to make.
