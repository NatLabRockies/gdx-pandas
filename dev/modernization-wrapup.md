# Closing out the modernization arc — four open issues

## Context

The `gams.transfer`-engine arc landed in v3.0.0 ([PR #112](https://github.com/NatLabRockies/gdx-pandas/pull/112), merge d43977b), which also renamed `backend` → `engine` everywhere and flipped the default engine to `gams.transfer` ([src/gdxpds/_engine.py:52](../src/gdxpds/_engine.py#L52)). With that dual-engine write path now the default, four open issues are good candidates for cleanup:

- **#39** — GAMS `eps` is mapped to `np.finfo(float).eps` (machine epsilon, ~2.22e-16). Conceptually wrong (machine epsilon is unrelated to GAMS's "infinitesimal" semantics), and silently causes false positives: any DataFrame value ≤ ~2.22e-16 round-trips as GDX EPS even if the user wrote a legitimately small float.
- **#65** — `to_gdx` consumes ~18GB RAM for a workload producing a 0.4GB GDX (2019, never resolved). Driven by the same per-symbol Python-level allocation hotspots that #113 targets.
- **#75** — `to_gdx` reportedly reorders set elements on write (2020). Not currently pinned by any regression test, and the default engine has changed since the report.
- **#106** — `GdxSymbol.domain` setter accepts any `GdxSymbol` reference as a strict-domain parent; should require Set or Alias-of-Set.
- **Docs nit (related to #106)** — overview.md frames domains as a "Subset (Domain)" concept, but Parameters/Variables/Equations can (and routinely do) have Set domains. The docs should make this explicit and rename the section accordingly.

Current release is **v3.0.0**. Non-breaking changes land first as **v3.1.0**, breaking changes as **v4.0.0**.

There is already a textbook precedent for #106's validation in the same file: the `alias_of` setter ([src/gdxpds/gdx.py:1220-1242](../src/gdxpds/gdx.py#L1220-L1242)) does exactly the Set-or-Alias parent-type check we need to mirror.

---

## Release plan

### v3.1.0 — non-breaking (perf + docs)

#### Item A — close out #113 (perf, write paths) and absorb #65 (memory)

Both engines still allocate a full per-symbol DataFrame copy plus per-column intermediate arrays. The same hotspots drive CPU (#113) and RAM (#65). One PR, expanded acceptance criteria.

Files to modify:

- [src/gdxpds/_gdxcc_engine.py](../src/gdxpds/_gdxcc_engine.py) — replace the per-row `[str(x) for x in row[:num_dims]]` + `itertuples` + per-value `isinstance(v, Number)` loop with a vectorized pre-pass:
  - Pre-stringify dim columns once into a 2D string array (e.g. `df.iloc[:, :num_dims].astype(str).to_numpy()`).
  - Pre-cast value columns to `float64`, substituting the UNDEF magic float in one vectorized pass. **Subtlety to preserve:** [src/gdxpds/special.py:83-114](../src/gdxpds/special.py#L83-L114)'s `convert_np_to_gdx_svs` uses pandas `.replace` which doesn't substitute on `None` keys — the current per-value `if v is None` is load-bearing. The pre-cast pass must handle `None`/UNDEF explicitly.
  - Reduce the inner loop to a single tight `for r in range(N)` with no per-value Python work.
- [src/gdxpds/_transfer_engine.py](../src/gdxpds/_transfer_engine.py) — profile and reduce per-symbol copies in the gams.transfer write path. Likely candidates: pass-through where the DataFrame can already be handed to `gt.Parameter(records=...)` without `.copy()`, and consolidate the per-value-column work in `_np_to_transfer_specials` into one vectorized pass.

Reuse / don't reinvent:

- The `_GdxHandle` RAII pattern in [src/gdxpds/tools.py](../src/gdxpds/tools.py) — no new lifecycle code needed.
- Existing benchmark scaffold [tests/test_engine_timing.py](../tests/test_engine_timing.py) — extend with a memory measurement (`psutil.Process().memory_info().rss` deltas around the write call) and a programmatically built large-row fixture (~1–5M rows) so we can amplify per-symbol allocation pressure without checking a large GDX into the repo.

Acceptance:

- gdxcc write loop within ~1.3× of bare `gdxDataWriteStr` at 1M rows (per #113).
- gams.transfer-mediated write within ~1.5× of raw `gams.transfer` on the 145 MB reference workload (per #113).
- **Peak RSS during write of a synthetic 5M-row Parameter ≤ 3× the on-disk size of the resulting GDX**, on both engines (current 18 GB / 0.4 GB ≈ 45× per #65; 3× is a defensible target after the per-symbol copy is removed).
- All existing cross-engine parity tests pass ([tests/test_engine_parity.py](../tests/test_engine_parity.py), [tests/test_handle_lifecycle.py](../tests/test_handle_lifecycle.py), UNDEF/NaN/EPS round-trip, alias-chain shape).

#### Item B — investigate and resolve #75 (set element ordering)

No existing test pins set element order on write, and the default engine flipped to `gams.transfer` in v3.0.0 — so the 2020 report may or may not still hold.

1. Add a regression test (suggested: extend [tests/test_engine_parity.py](../tests/test_engine_parity.py) or new `tests/test_set_ordering.py`) that round-trips the issue's exact example (Set with rows `[2008, 2010, 2015, 2020]`) through `to_gdx` → `to_dataframes` for **both** engines (use the existing engine parametrize pattern).
2. If both engines preserve order → close #75 as fixed, ship the regression test as evidence.
3. If either engine reorders → diagnose:
   - **gdxcc engine** — iterates rows in DataFrame order and registers UELs implicitly via `gdxDataWriteStr`. Apparent reorder on read is typically driven by the global UEL pool's first-encounter order (if another set in the file introduced 2010/2015/2020 first, those get earlier UEL indices). If this is the cause, document the semantics in overview.md and recommend appending the set whose order matters first.
   - **transfer engine** — verify whether `gt.Parameter(records=...)` / `gt.Set(records=...)` preserves record order. If not, document the limitation; if yes, only the gdxcc engine gets the documentation note.

Acceptance:

- Regression test exists and is parametrized over both engines.
- Behavior is documented in [doc/source/overview.md](../doc/source/overview.md), folded into the renamed "Domain Relationships" section (Item C below) or the existing engine-differences note around [overview.md:274](../doc/source/overview.md#L274).

#### Item C — docs nit (Parameter/Variable/Equation domains)

The code model is already correct ([src/gdxpds/gdx.py:567-571](../src/gdxpds/gdx.py#L567-L571) documents `REGULAR` as "each non-wildcard dimension references an existing Set/Alias") but the user-facing docs frame domain as a Set-on-Set concept.

Files to modify:

- [doc/source/overview.md:224](../doc/source/overview.md#L224) — rename the section heading "Subset (Domain) Relationships" → "Domain Relationships" and rewrite the lead paragraph to make explicit:
  - Any symbol type (Set, Parameter, Variable, Equation) can declare a strict domain.
  - The *parent* in each domain slot must be a Set or Alias-of-Set — this is what GAMS's `gdxSymbolSetDomain` enforces.
  - Set-on-Set is a *subset* relationship (`set sub_a(a)` means `sub_a` ⊆ `a`); Parameter/Variable/Equation-on-Set is an *indexed-over* relationship (`parameter p(a)` means `p` is defined on `a`).
  - Both cases use the same `GdxSymbol.domain` attribute and the same strict-write path.
- Add a Parameter-on-Set example next to the existing Set-on-Set example block at [overview.md:244-262](../doc/source/overview.md#L244-L262).
- [src/gdxpds/__init__.py](../src/gdxpds/__init__.py) and [src/gdxpds/write_gdx.py](../src/gdxpds/write_gdx.py) — the `to_gdx` `domains=` docstring (and the public `get_subset_relationships` docstring at [overview.md:47](../doc/source/overview.md#L47)) say "subset/domain". Soften the framing to just "domain" and add one sentence noting parents must be Sets/Aliases.
- Update the Special-values mapping at [overview.md:12](../doc/source/overview.md#L12) and [overview.md:358](../doc/source/overview.md#L358) only with a forward-reference: "EPS handling is changing in v4.0.0 — see Item D." Avoid duplicating the eventual v4.0.0 doc change here.

Acceptance:

- `cd doc; .\make.bat html` builds without new warnings.
- The Parameter-on-Set example actually round-trips (manually executed, or wired into a sphinx doctest).

---

### v4.0.0 — breaking (semantic correctness)

#### Item D — #39 EPS → `np.finfo(float).tiny`

Files to modify:

- [src/gdxpds/special.py:10](../src/gdxpds/special.py#L10) — change `NUMPY_SPECIAL_VALUES = [None, np.nan, np.inf, -np.inf, np.finfo(float).eps]` to `[None, np.nan, np.inf, -np.inf, np.finfo(float).tiny]`.
- [src/gdxpds/special.py:53-65](../src/gdxpds/special.py#L53-L65) — re-examine `is_np_eps`. Current logic `np.abs(val - eps) < eps` worked because eps was ~2.22e-16 — there was slack for floating-point drift. With tiny ~2.22e-308 the tolerance becomes punishingly tight, but that's the *right* answer: only an exact `tiny` should round-trip as GDX EPS. Replace with `val == NUMPY_SPECIAL_VALUES[-1]` (exact equality) so legitimate small floats like `1e-200` are no longer false-positives for EPS.
- [src/gdxpds/special.py:109-110](../src/gdxpds/special.py#L109-L110) — same change in `convert_np_to_gdx_svs`: replace the band detection `(values - eps).abs() < eps` with exact equality. This also removes a subtle bug: today, *any* DataFrame value ≤ machine epsilon silently round-trips as GAMS EPS, which is not what users expect.
- [src/gdxpds/_transfer_engine.py](../src/gdxpds/_transfer_engine.py) — apply the same exact-equality change in the `_np_to_transfer_specials` equivalent (the gams.transfer engine has its own EPS-detection band).

Tests to update / add:

- [tests/test_specials.py](../tests/test_specials.py) — replace `np.finfo(float).eps` with `np.finfo(float).tiny` in all EPS round-trip assertions.
- [tests/test_engine_parity.py](../tests/test_engine_parity.py) — same substitution.
- **New regression test:** writing `1e-200` to GDX must round-trip as `1e-200`, not as EPS. (Today this silently fails because `1e-200 < machine_eps`.) This is the strongest single-sentence argument for the change in CHANGES.txt.

Docs:

- [doc/source/overview.md:12](../doc/source/overview.md#L12), [overview.md:358](../doc/source/overview.md#L358), [overview.md:363-368](../doc/source/overview.md#L363-L368) — update the special-value mapping table and "drop EPS" snippet so `eps = np.finfo(float).tiny`.
- [CHANGES.txt](../CHANGES.txt) — flag as breaking under v4.0.0, with the `1e-200` example as the rationale.

#### Item E — #106 strict-domain parent must be Set or Alias

Single-point validation in the `GdxSymbol.domain` setter, mirroring the existing pattern in the `alias_of` setter.

Files to modify:

- [src/gdxpds/gdx.py:1063-1099](../src/gdxpds/gdx.py#L1063-L1099) — in the `domain` setter, after the existing `isinstance(d, GdxSymbol)` check at line 1082, add (modelled on the `alias_of` setter at [src/gdxpds/gdx.py:1236-1240](../src/gdxpds/gdx.py#L1236-L1240)):

  ```python
  if d.data_type not in (GamsDataType.Set, GamsDataType.Alias):
      raise DomainError(
          f"domain parent must be a Set (or another Alias); "
          f"{d.name!r} is a {d.data_type.name}."
      )
  ```

  Place it before the `len(value) != self.num_dims` block so the type error wins over a length-mismatch error. Per the issue: the setter is the single chokepoint every strict-domain assignment flows through, so `__wire_domains` in [src/gdxpds/write_gdx.py:155-167](../src/gdxpds/write_gdx.py#L155-L167) doesn't need its own duplicate check.

- **Validate against gdxcc first.** Before merging, confirm with a small script (against the local GAMS install) that `gdxcc.gdxSymbolSetDomain` accepts Alias-of-Set parents. [src/gdxpds/gdx.py:567-571](../src/gdxpds/gdx.py#L567-L571) asserts this in the `GamsDomainType.REGULAR` docstring and the `alias_of` setter accepts an Alias-of-Alias chain, but gdxcc's actual behavior on writing an Alias as a domain parent should be verified empirically. If Alias doesn't round-trip, narrow the rule to `Set` only.

Tests to add (in [tests/test_domain.py](../tests/test_domain.py)):

- Assigning `domain` whose parent is a Parameter raises `DomainError` at assignment time, not at write time. Same for Variable and Equation parents.
- Positive: a Set parent works (current behavior, regression).
- Positive (gated on the gdxcc verification above): an Alias-of-Set parent works and the resulting GDX round-trips its `domain_type` as `REGULAR`.

Docs:

- [doc/source/overview.md](../doc/source/overview.md) — in the v3.1.0-renamed "Domain Relationships" section, append a note that strict-domain assignment validates parent type at assignment as of v4.0.0+ (previously, non-Set parents were silently relaxed to `RELAXED` at write time).
- [CHANGES.txt](../CHANGES.txt) — flag as breaking under v4.0.0.

---

## Verification

Pre-merge for **v3.1.0**:

```powershell
.venv\Scripts\Activate.ps1
pip install -e .[test]
pytest tests                              # existing parity + lifecycle + ordering tests
pytest tests/test_engine_timing.py -v     # confirm new perf + memory thresholds met
cd doc; .\make.bat html                   # docs build cleanly with renamed "Domain Relationships" section
ruff check . ; ruff format --check . ; pyright
gdxpds info ; gdxpds test                 # smoke check the CLI/install entry points
```

Pre-merge for **v4.0.0** (in addition to the above):

```powershell
pytest tests/test_specials.py             # EPS remap exact-equality tests pass
pytest tests/test_engine_parity.py        # both engines agree on the new EPS value
pytest tests/test_domain.py               # Parameter/Variable/Equation parent -> DomainError at assignment

# Hard regression: legitimately small floats no longer false-positive as EPS
python -c "import gdxpds, numpy as np, pandas as pd; \
  fp='tmp.gdx'; \
  gdxpds.to_gdx({'p': pd.DataFrame([['x', 1e-200]], columns=['i','Value'])}, fp); \
  v = float(gdxpds.to_dataframes(fp)['p']['Value'][0]); \
  assert v == 1e-200, v; print('OK', v)"
```

Run both gdxcc and gams.transfer engines for each release via the existing `GDXPDS_ENGINE` env-var hook ([src/gdxpds/_engine.py:165](../src/gdxpds/_engine.py#L165)).

---

## Open questions to resolve during execution

1. **Alias-of-Set as a domain parent in gdxcc.** [src/gdxpds/gdx.py:570](../src/gdxpds/gdx.py#L570) docstring says it's accepted; the `alias_of` setter accepts Alias parents already. Confirm gdxcc's behavior with a small `gdxSymbolSetDomain` script against the local GAMS install before pinning the #106 validation rule, and decide whether to admit Alias parents or restrict to Set only.
2. **#75 outcome.** Whether the issue gets closed-as-fixed or closed-with-docs is determined by what the regression test shows on the v3.0.0 default engine (gams.transfer). The plan covers both branches.
3. **#65 measurement methodology.** Whether `psutil`-based RSS deltas are reliable enough on Windows / inside pytest fixtures to gate the 3× target — if not, swap for `tracemalloc.get_traced_memory()` peak before signing off.
