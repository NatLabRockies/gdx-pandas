# gdxpds release roadmap

**Status:** active. Single source of truth for how the post-v1.5.0 work is staged
into releases. Update this file as releases ship.

## Release map

```
1.6.0  typing / tooling  (ruff + pyright + pre-commit, py.typed, public-API
       annotations) + DeprecationWarning on the to_dataframe legacy dict default
  |
  v
2.0.0  breaking cleanup:
         - remove deprecated .py CLI shims
         - remove gdx2py accelerator (no user base, not an API break)
         - to_dataframe always returns a plain DataFrame
  |
  |  [test-gap groundwork lands on main — correctness oracle for the speedup]
  v
2.1.0  gams.transfer read fast path (opt-in, non-breaking)
       greenfield accelerator — gated by an evaluation spike first
```

Versioning is strict SemVer. `__version__` is single-sourced in
[../src/gdxpds/__init__.py](../src/gdxpds/__init__.py) and read by
[../pyproject.toml](../pyproject.toml) via `dynamic = ["version"]`. Each release =
bump `__version__`, add a [../CHANGES.txt](../CHANGES.txt) entry, run the local
GAMS-matrix tests, then publish a GitHub Release tagged `vX.Y.Z` (the tag must
equal `__version__`; automation handles PyPI + docs).

## PR / branch map

Each PR branches off `main`, in order:

| Branch | Release | Scope |
|--------|---------|-------|
| `eh/ruff-typing-tooling` | **v1.6.0** | typing + tooling; finish with the `to_dataframe` deprecation warning, version bump, CHANGES entry |
| e.g. `eh/breaking-cleanup` | **v2.0.0** | remove `.py` CLI shims, gdx2py, and `to_dataframe(old_interface=...)` |
| e.g. `eh/test-gaps` | _(no tag)_ | test-gap groundwork; the correctness oracle for the speedup |
| e.g. `eh/gams-transfer` | **v2.1.0** | evaluation spike first (throwaway), then the read fast path |

Ordering: the test-gap PR lands before the speedup PR — those tests are the
correctness oracle for the backend swap. The breaking-cleanup PR is independent
and goes first (cleanup-first).

---

## v1.6.0 — typing + tooling

Implemented on `eh/ruff-typing-tooling` (ruff format + lint, pyright config,
`py.typed`, public-API annotations, lint CI, pre-commit). `py.typed` + the
annotations are a user-visible, backward-compatible feature (downstream type
checking), so this is a minor bump, not a patch.

The one functional addition is a deprecation notice: `to_dataframe` emits a
`DeprecationWarning` whenever it returns the legacy `{symbol_name: df}` dict (i.e.
`old_interface` truthy), pointing callers at `old_interface=False`. Callers already
passing `old_interface=False` see no warning. See
[../src/gdxpds/read_gdx.py](../src/gdxpds/read_gdx.py).

## v2.0.0 — breaking cleanup

The breaking release; bundles all the breaking debt in one place.

- **`.py` CLI shims** — delete [../bin/](../bin/), drop the `[tool.setuptools]
  script-files = [...]` block from [../pyproject.toml](../pyproject.toml), and
  remove `main_py_alias()` from
  [../src/gdxpds/cli/csv_to_gdx.py](../src/gdxpds/cli/csv_to_gdx.py) and
  [../src/gdxpds/cli/gdx_to_csv.py](../src/gdxpds/cli/gdx_to_csv.py). The modern
  `csv_to_gdx` / `gdx_to_csv` entry points are unaffected (already covered by the
  subprocess round-trip tests).
- **gdx2py** — remove the import/flag and the Parameter fast branch in
  [../src/gdxpds/gdx.py](../src/gdxpds/gdx.py) and its line in `info()`
  ([../src/gdxpds/tools.py](../src/gdxpds/tools.py)). No deprecation cycle: no real
  gdx2py user base, and it is an optional try/except-detected accelerator, so
  removal is not an API break. Affected Parameter reads fall back to the
  `gdxDataReadStr` slow path until the gams.transfer accelerator lands in 2.1.0.
- **`to_dataframe(old_interface=...)`** — drop the kwarg so the function always
  returns a plain DataFrame (callers passing `old_interface=False` just delete the
  kwarg). Update [../doc/source/overview.md](../doc/source/overview.md).

Internal-only cleanup candidate (not user-facing; can defer to the 2.1.0 read-path
work): the legacy `Translator` classes in
[../src/gdxpds/read_gdx.py](../src/gdxpds/read_gdx.py) /
[../src/gdxpds/write_gdx.py](../src/gdxpds/write_gdx.py) are not in `__all__` —
consider folding into `GdxFile`/`GdxSymbol`.

## Test-gap groundwork (between 2.0.0 and 2.1.0, no release of its own)

Tests don't ship in the wheel, so they don't warrant a version bump. Their purpose
is to be the **correctness oracle** for the 2.1.0 backend swap: gams.transfer
output must match the current gdxcc output exactly. Priorities:

- Round-trip + value assertions for **Variables and Equations** (the five
  Level/Marginal/Lower/Upper/Scale columns) and **Aliases**.
- A test for **set text** (`load_set_text=True` / `gdxGetElemText()`).
- Broader **special-value** edge cases (set membership booleans, Parameters with
  NaN columns, `_fixup_set_vals`).
- **Error / malformed paths** (corrupt/truncated GDX; write-side DataFrame shape
  mismatches).

Reuse the existing fixtures and real `.gdx`/`.csv` files under `../tests/`.

## v2.1.0 — gams.transfer read fast path (greenfield accelerator)

The headline performance feature: an opt-in, non-breaking read accelerator using
`gams.transfer` (ships inside `gamsapi`, cross-platform).

**Why this is riskier than gdx2py was.** gdx2py was a clean drop-in (returned a
plain list). gams.transfer is not:

- *Different data model → translation layer required.* It reads into a `Container`
  with its own column names (`level/marginal/lower/upper/scale`, `value`),
  categorical domains, and its own special-value handling. gdxpds expects specific
  columns (`Value`; `Level/Marginal/Lower/Upper/Scale`), boolean set *membership*,
  element text, and the magic-float conversion in
  [../src/gdxpds/special.py](../src/gdxpds/special.py). Mapping one to the other
  exactly is the bulk of the risk.
- *Whole-container vs. lazy-per-symbol read.* gdxpds is lazy; gams.transfer's
  advantage is bulk reading. Reconciling the two touches the lazy-loading model.

**Step 0 — evaluation spike (gate; throwaway code, no release).** Read the
`../tests/` fixtures with both the current gdxcc path and a gams.transfer
prototype; diff the resulting DataFrames column-by-column; benchmark both. Proceed
only if the speedup is material **and** every divergence is explainable. Otherwise
keep the slow path and abandon — the only cost was the spike.

**If it proceeds (conservative shape):** read path only, behind a
`HAVE_GAMS_TRANSFER` capability flag in `GdxSymbol.load()`
([../src/gdxpds/gdx.py](../src/gdxpds/gdx.py)), with the `gdxDataReadStr` slow path
as the always-present fallback and correctness oracle (fast path must equal slow
path). Keep the translation layer isolated and unit-tested against slow-path
output. **Do not touch the write path** — it is tightly coupled to
`gdxDataWriteStr*` and is where divergence risk is highest. Surface availability in
`info()`.
