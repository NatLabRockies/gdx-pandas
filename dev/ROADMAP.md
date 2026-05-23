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
2.1.0  gams.transfer fast path — read AND write, opt-in & non-breaking,
       behind a backend switch. Spike done (~87x); strict parity vs gdxcc.
  |
  v
3.0.0  breaking, coordinated:
         - flip default backend to gams.transfer (when available)
         - add set-text-write (gams.transfer provides it natively)
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
| `eh/test-gaps` | _(no tag)_ | test-gap groundwork; the correctness oracle for the speedup — **landed** (PR #109) |
| `eh/gams-transfer` | **v2.1.0** | Phase 0 (gdxcc extracted) + Step 1 (backend switch) + Phase A (read) + Phase B (write) **done**; version bumped + CHANGES written — ready to merge and tag |
| e.g. `eh/gams-transfer-default` | **v3.0.0** | flip default to gams.transfer + add set-text-write (breaking, coordinated) + full status for Alias |

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

**Status: landed on `main` (PR #109).**

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

## v2.1.0 — gams.transfer fast path, read + write (greenfield accelerator)

The headline performance feature: an opt-in, non-breaking accelerator using
`gams.transfer` (ships inside `gamsapi`, cross-platform), covering **both read and
write**, selected at runtime by a backend switch. The current gdxcc path stays the
default and the correctness oracle.

**Spike done — gate satisfied.** Measured on a real ReEDS-sized GDX
(`inputs-v20250926_mainK0_USA_defaults.gdx`, read into DataFrames then written
back): read 776.6 s → 9.0 s (~86x), write 1002.0 s → 11.5 s (~87x). The "proceed
only if the speedup is material" gate is decisively met — which is why this release
now also covers writes, not just reads.

**Status (branch `eh/gams-transfer`, complete).** Phase 0 (gdxcc
extracted behind a `GdxBackend` ABC; set-text reads unified), Step 1 (`Backend`
enum + `backend=` kwarg + `GDXPDS_BACKEND` env var + `HAVE_GAMS_TRANSFER` +
`to_dataframes(symbols=...)` subset + `BackendError`/`SymbolNotFoundError`),
**Phase A** (the gams.transfer read backend, parity-tested vs gdxcc over all
fixtures incl. set text, special values, subset, and aliases), and **Phase B**
(the gams.transfer write backend, parity-tested over the full write × read
backend matrix). Two read-side decisions firmed up during Phase A:

- *Aliases read as Sets* (both backends). Legacy gdxpds read an alias as a
  degenerate float column — an untested/unused path; it now reads like the set it
  aliases (`c_bool` membership), so it shares the Set membership-boolean wart and
  is fixed together with it in v3.0.0.
- *`HAVE_GAMS_TRANSFER` means usable, not merely importable.* The probe constructs
  a Container, so a version-skewed gamsapi (imports but can't load the GAMS shared
  libraries) reads as unavailable and transfer-gated tests skip cleanly rather
  than crashing. `info()` reports `gams.transfer usable: yes/no`.

**Done:** all phases, plus the v2.1.0 version bump (`__version__` = 2.1.0) and the
CHANGES.txt entry (05/23/26). Ready to merge to `main` and publish the `v2.1.0`
Release; the local GAMS-matrix test run is the remaining gate.

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

**Shape (symmetric backends; both phases opt-in, switchable, strict parity).**

- *Phase 0 — extract the gdxcc backend first.* Before adding gams.transfer,
  refactor so [../src/gdxpds/gdx.py](../src/gdxpds/gdx.py) is a backend-agnostic
  interface + data model that delegates I/O to a `GdxBackend` (an ABC whose read
  primitive is `load_symbols(names | None)`, with `load_file`/`load_symbol` as
  conveniences, plus `write_file`/`close`). The existing gdxcc logic moves
  to `_gdxcc_backend.py`; gams.transfer lands as a sibling `_transfer_backend.py`.
  Also unifies set-text reads into the same `load_file`/`load_symbol` paradigm
  (removing the read_gdx.py lazy-loop special case). Pure, behavior-preserving
  refactor (existing tests are the oracle); ships as its own PR. Avoids a
  permanent gdxcc/transfer asymmetry.
- *Backend switch.* A public `Backend` str-enum (`GDXCC`, `GAMS_TRANSFER`); a
  `backend=` kwarg on `GdxFile` and the top-level read/write helpers, with a
  `GDXPDS_BACKEND` env-var fallback (kwarg wins). Single `DEFAULT_BACKEND`
  constant (= `GDXCC`); **no `"auto"` value** — an explicit gams.transfer request
  that can't be satisfied raises. A `HAVE_GAMS_TRANSFER` capability flag and the
  resolved default backend surface in `info()`.
- *Phase A — read.* Translation layer from `gt.Container` records to the existing
  DataFrame shape (column names, `c_bool` set membership, special-value mapping).
  `load_file` is one bulk `c.read(records=True)`; lazy/subset reads map to a
  targeted `c.read(symbols=...)`.
- *New feature — symbol subset.* `to_dataframes(symbols=[...])` reads only the
  named symbols (fills the gap between `to_dataframe` and `to_dataframes`).
  Non-breaking (default = all); a single targeted read on gams.transfer, a loop
  on gdxcc.
- *Phase B — write.* Translation layer from gdxpds DataFrames to a `gt.Container`,
  then `Container.write()`. Reuses the existing type-inference in
  [../src/gdxpds/write_gdx.py](../src/gdxpds/write_gdx.py) unchanged; only the
  serialize-to-disk step changes.
- *Strict parity is the contract.* The fast path must equal the gdxcc path
  exactly, verified by backend-parametrized parity tests over every fixture (fast
  path == slow path, both directions). Keep the translation layer isolated and
  unit-tested against gdxcc output.

**Deliberately preserved in v2.1.0:** Phase B does **not** write Set element text —
it emits empty `element_text`, matching the current gdxcc path (which has no
`gdxAddSetText` and silently drops text). This keeps the two backends byte-identical.
Set-text-write is a behavior change, deferred to v3.0.0 (below). The detailed
implementation design (translation per symbol kind, special-value bit-pattern
handling, lazy/eager behavior, risk register) lives in the working plan file, not
here.

## v3.0.0 — default-flip to gams.transfer + set-text-write (breaking)

One coordinated breaking release, once v2.1.0's parity tests have run green for a
cycle. The ~87x speedup makes indefinite opt-in untenable — users will expect the
speedup by default — and the set-text-write fix is breaking, so the two land
together under one major bump (one migration note, not two).

- **Flip the default backend** to gams.transfer when `HAVE_GAMS_TRANSFER` — an
  honest one-line change of the `DEFAULT_BACKEND` constant. gdxcc-only
  environments are unaffected; `backend="gdxcc"` remains available to pin the old
  path.
- **Add set-text-write.** gdxpds gains the ability to write Set element text — a
  capability the gdxcc path never had (no `gdxAddSetText`). The gams.transfer write
  path provides it natively: the v2.1.0 "emit empty `element_text`" step flips to
  "write the loaded text column." Because the default is now gams.transfer, this
  becomes default behavior. This is the breaking change the "Known warts" set-text
  item calls for.

Candidate third payload for the same release: fixing the membership-boolean wart
for **Sets and Aliases** (`Value` reliably `True` for members) — also breaking,
also touches read + write + `load_set_text`. See "Known warts" below.

## Known warts / deferred cleanups

Decide deliberately when these are touched. (The one below is now slated for
v3.0.0; future entries may be unscheduled.)

- **A Set's membership boolean is the stored value's truthiness, not "is a
  member".** The `Value` column reads `c_bool(False)` for plain membership
  (gdxpds- and GAMS-written Sets store `0.0`) and only `c_bool(True)` when a
  non-zero value happens to be stored (e.g. a set-text node index). Membership is
  really conveyed by row *presence*, so the boolean is misleading.
  `_fixup_set_value` ([../src/gdxpds/gdx.py](../src/gdxpds/gdx.py)) leaves the
  written value at `0.0` because `isinstance(c_bool(True), Number)` is False. The
  current behavior is now pinned by tests in
  [../tests/test_read.py](../tests/test_read.py) so the gams.transfer backend
  can't drift. **Aliases now read as Sets (v2.1.0), so they share this wart** —
  the fix must cover Set and Alias together. Fixing it (membership reliably
  `True`) is a deliberate behavior change touching the read path, the write path,
  and `load_set_text` — coordinate with the gams.transfer work and treat it as
  breaking. **Slated for v3.0.0** as a candidate payload alongside the
  default-flip and set-text-write.

- **GDX UNDEF is not preserved on write — it collapses to `0.0`.** gdxpds'
  canonical form maps GDX UNDEF → Python `None` and GDX NA → `np.nan` (see
  [../src/gdxpds/special.py](../src/gdxpds/special.py),
  `NUMPY_SPECIAL_VALUES`/`GDX_TO_NP_SVS`). Both backends *read* the distinction
  correctly (a value column carrying any UNDEF comes back as object dtype with
  `None`; v2.1.0 made the gams.transfer read match the gdxcc oracle here). But the
  gdxcc *write* path cannot emit UNDEF: a `None` isn't a `Number`, so
  `write_symbol` ([../src/gdxpds/_gdxcc_backend.py](../src/gdxpds/_gdxcc_backend.py))
  falls through to `0.0`. So a read→write round-trip turns UNDEF into `0.0` (NA, by
  contrast, round-trips). For strict v2.1.0 parity the gams.transfer write path
  mirrors this — `_np_to_transfer_specials`
  ([../src/gdxpds/_transfer_backend.py](../src/gdxpds/_transfer_backend.py)) maps
  `None` → `0.0` rather than to a genuine `gt.SpecialValues.UNDEF` — pinned by
  `test_write_parity_undef`
  ([../tests/test_backend_parity.py](../tests/test_backend_parity.py)). **Candidate
  fix for v3.0.0** (unscheduled): gams.transfer *can* write a real UNDEF (which
  reads back as `None`), so the default-flip release could let the transfer write
  preserve UNDEF. It's a behavior change that would diverge from the gdxcc oracle,
  so it belongs with the coordinated breaking release, not v2.1.0.

- **`GdxFile.H` is a gdxcc-specific escape hatch on an engine-agnostic
  interface.** After the Phase 0 extraction it delegates to
  `self._backend_impl.handle` — the gdxcc GDX pointer, or `None` for backends
  without one (and after `cleanup`). It stays public and working in v2.1.0 (it's
  used as a raw-`gdxcc` escape hatch, e.g. in
  [../tests/test_specials.py](../tests/test_specials.py)). **Candidate for
  deprecation/removal in v3.0.0**, when the default flips to gams.transfer and
  `None` becomes the common return; power users would move to
  `gdx_file._backend_impl.handle` or a documented accessor.
