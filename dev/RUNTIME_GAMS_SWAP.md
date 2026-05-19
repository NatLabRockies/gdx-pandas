# Runtime GAMS swap (feasible; not implemented)

Design notes for an in-process GAMS swap, should we ever want one.
Kept out of [CLAUDE.md](../CLAUDE.md) to keep that file lean.

The "one GAMS library bound per process" constraint is a property of
`gdxpds`, not of the underlying C bindings. `gams.core.gdx` exposes two
primitives that together would allow swapping GAMS at runtime:

- `gdxLibraryLoaded() -> int` — returns 1 if the GDX shared library is
  currently bound to the process, 0 otherwise.
- `gdxLibraryUnload() -> int` — unloads the bound library; returns 1 on
  success.

Verified behavior on Windows with `gamsapi 48.7.0`:

1. Before any `import gdxpds`: `gdxLibraryLoaded() == 0`.
2. After the first GDX op (first `_create_gdx_object` call):
   `gdxLibraryLoaded() == 1`.
3. After `gdxLibraryUnload()`: returns 1, and `gdxLibraryLoaded() == 0`.
4. A subsequent `gdxCreateD(H, "/different/GAMS", ...)` re-loads from
   the new directory: rc=1, `gdxLibraryLoaded() == 1`.

So an in-process GAMS swap is technically reachable. The recipe would
be: close all open handles → `gdxLibraryUnload()` → reset
`tools._loaded_gams_dir = None` and `tools._bindings_source = None` →
fresh `load_gdxcc(new_dir)`.

## Caveats that would need verification before relying on this

1. **Stale handles.** Any `GdxFile` / raw `H` handles created against
   the previous library reference unloaded memory after unload.
   Operations on them likely segfault. A real implementation needs to
   find and close them all, or refuse to unload while any are open.
2. **Cold-start crash risk re-emerges.** The first `gdxCreateD` after
   unload faces the same "non-GAMS dir → access violation on Windows"
   failure mode as a cold-start. `_require_gams_installation` in
   [../src/gdxpds/tools.py](../src/gdxpds/tools.py) already runs in
   `load_gdxcc()`, so calling `load_gdxcc(new_dir)` after unload gives
   the right behavior — but any code that calls `gdxCreateD` directly
   (not through `load_gdxcc`) would need to add the pre-check.
3. **`gdxpds.special` state.** [../src/gdxpds/special.py](../src/gdxpds/special.py)
   populates `SPECIAL_VALUES`, `GDX_TO_NP_SVS`, `NP_TO_GDX_SVS`
   module-level dicts from the loaded library. These are GDX-format
   constants and likely identical across GAMS versions, but a
   reload-aware API should re-call `load_specials()` for hygiene.
   `load_gdxcc()` does this on first bind; resetting
   `_loaded_gams_dir = None` will cause the next call to take the
   first-bind branch and re-populate.
4. **`load_gdxcc()` is idempotent but not reload-aware.** Its
   fast-path (when `_loaded_gams_dir is not None`) skips both the
   rebind and `load_specials`, just emitting the mismatch warning. A
   `reload_gdxcc()` entry point would call `gdxLibraryUnload()`
   first, clear `_loaded_gams_dir` and `_bindings_source`, then
   `load_gdxcc(new_dir)` to drive the full first-bind path.
5. **Linux semantics.** All verification above is Windows-only.
   `dlclose()` on Linux is famously unreliable for "really fully
   unload" — the kernel may keep the library mapped if any
   references remain. The Linux load-order pass (NLR HPC, GAMS
   34/49/51) did *not* exercise unload behavior; that still needs
   its own verification.

A shape for a future `gdxpds.reload_gdxcc(gams_dir)` would: assert no
live handles → call `gdxLibraryUnload()` → set `_loaded_gams_dir =
None`, `_bindings_source = None` → `load_gdxcc(gams_dir)`. Worth
designing on Linux where multi-version testing is the natural pressure
for in-process swap.
