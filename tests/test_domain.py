"""Tests for subset (domain) relationships between Sets.
"""
import logging
import os

import pandas as pd
import pytest

import gdxpds
from gdxpds.gdx import (
    DomainError,
    GamsDataType,
    GamsDomainType,
    GdxFile,
    GdxSymbol,
)

logger = logging.getLogger(__name__)


def _set_df(rows, col="t"):
    return pd.DataFrame([[r, True] for r in rows], columns=[col, "Value"])


def _make_parent_child(parent_name="t", child_name="sub_t",
                       parent_rows=("a", "b", "c"),
                       child_rows=("a", "c"),
                       strict=True):
    """Return a new GdxFile with a parent Set and child subset."""
    gdx = GdxFile()
    parent = GdxSymbol(parent_name, GamsDataType.Set, dims=[parent_name])
    gdx.append(parent)
    gdx[-1].dataframe = _set_df(parent_rows, col=parent_name)

    if strict:
        child = GdxSymbol(child_name, GamsDataType.Set, dims=[parent_name],
                          domain=[gdx[parent_name]])
    else:
        child = GdxSymbol(child_name, GamsDataType.Set, dims=[parent_name])
    gdx.append(child)
    gdx[-1].dataframe = _set_df(child_rows, col=parent_name)
    return gdx


# ---------------------------------------------------------------------------
# 1. Strict round-trip
# ---------------------------------------------------------------------------
def test_strict_round_trip(run_dir):
    out = os.path.join(run_dir, "strict_round_trip.gdx")
    with _make_parent_child() as gdx:
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        sub = gdx["sub_t"]
        assert sub.domain_type == GamsDomainType.REGULAR
        assert sub.domain is not None
        assert sub.domain[0] is gdx["t"]
        assert sub.dims == ["t"]


# ---------------------------------------------------------------------------
# 2. Relaxed via dims (no domain)
# ---------------------------------------------------------------------------
def test_relaxed_via_dims(run_dir):
    out = os.path.join(run_dir, "relaxed_via_dims.gdx")
    with _make_parent_child(strict=False) as gdx:
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        sub = gdx["sub_t"]
        assert sub.domain_type == GamsDomainType.RELAXED
        assert sub.domain is None
        assert sub.dims == ["t"]


# ---------------------------------------------------------------------------
# 3. Wildcard -> NONE
# ---------------------------------------------------------------------------
def test_wildcard_is_none(run_dir):
    out = os.path.join(run_dir, "wildcard_is_none.gdx")
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("u", GamsDataType.Set, dims=["*"]))
        gdx[-1].dataframe = _set_df(["u1", "u2"], col="*")
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        assert gdx["u"].domain_type == GamsDomainType.NONE
        assert gdx["u"].domain is None


# ---------------------------------------------------------------------------
# 4. Wildcard inside a strict domain
# ---------------------------------------------------------------------------
def test_wildcard_inside_strict(run_dir):
    out = os.path.join(run_dir, "wildcard_inside_strict.gdx")
    with GdxFile() as gdx:
        parent = GdxSymbol("a", GamsDataType.Set, dims=["a"])
        gdx.append(parent)
        gdx[-1].dataframe = _set_df(["a1", "a2"], col="a")

        child = GdxSymbol("mix", GamsDataType.Set, dims=["a", "b"],
                          domain=[gdx["a"], None])
        gdx.append(child)
        gdx[-1].dataframe = pd.DataFrame(
            [["a1", "x", True], ["a2", "y", True]],
            columns=["a", "*", "Value"],
        )
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        mix = gdx["mix"]
        assert mix.domain_type == GamsDomainType.REGULAR
        assert mix.domain is not None
        assert mix.domain[0] is gdx["a"]
        assert mix.domain[1] is None
        assert mix.dims == ["a", "*"]


# ---------------------------------------------------------------------------
# 5. Self-reference set i(i) -> auto-downgrade to RELAXED
# ---------------------------------------------------------------------------
def test_self_reference_downgrades(run_dir):
    out = os.path.join(run_dir, "self_ref.gdx")
    with GdxFile() as gdx:
        i = GdxSymbol("i", GamsDataType.Set, dims=["i"])
        gdx.append(i)
        # Now assign the self-reference (i is its own domain).
        gdx["i"].domain = [gdx["i"]]
        gdx["i"].dataframe = _set_df(["i1", "i2"], col="i")
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        # When writing, strict was downgraded to relaxed because i isn't yet
        # in the GDX symbol table at the moment of its own WriteStart.
        assert gdx["i"].domain_type == GamsDomainType.RELAXED
        assert gdx["i"].dims == ["i"]


# ---------------------------------------------------------------------------
# 6. Wrong-length lists
# ---------------------------------------------------------------------------
def test_wrong_length_raises():
    with GdxFile() as gdx:
        parent_a = GdxSymbol("a", GamsDataType.Set, dims=["a"])
        gdx.append(parent_a)
        gdx[-1].dataframe = _set_df(["a1"], col="a")
        parent_b = GdxSymbol("b", GamsDataType.Set, dims=["b"])
        gdx.append(parent_b)
        gdx[-1].dataframe = _set_df(["b1"], col="b")

        child = GdxSymbol("c", GamsDataType.Set, dims=["a"])
        gdx.append(child)
        gdx[-1].dataframe = _set_df(["a1"], col="a")

        with pytest.raises(DomainError):
            gdx["c"].domain = [gdx["a"], gdx["b"]]  # wrong length
        with pytest.raises(DomainError):
            gdx["c"].dims = ["a", "b"]  # wrong length


# ---------------------------------------------------------------------------
# 7. domain setter rejects plain strings
# ---------------------------------------------------------------------------
def test_domain_setter_rejects_strings():
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("a", GamsDataType.Set, dims=["a"]))
        gdx[-1].dataframe = _set_df(["a1"], col="a")
        with pytest.raises(DomainError):
            gdx["a"].domain = ["a"]
        with pytest.raises(DomainError):
            gdx["a"].domain = ["not_a_real_set"]


# ---------------------------------------------------------------------------
# 8. Stale reference (parent removed) -> falls back to RELAXED
# ---------------------------------------------------------------------------
def test_stale_reference_falls_back(run_dir):
    out = os.path.join(run_dir, "stale_ref.gdx")
    with _make_parent_child() as gdx:
        # Remove the parent before writing.
        del gdx["t"]
        # The child still holds a ref to the (now-orphaned) parent symbol.
        assert gdx["sub_t"]._domain is not None
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        # Without the parent in the file, strict write was impossible;
        # we fell back to relaxed but preserved the dim name.
        assert gdx["sub_t"].domain_type == GamsDomainType.RELAXED
        assert gdx["sub_t"].dims == ["t"]


# ---------------------------------------------------------------------------
# 9. Cross-file reference -> falls back to RELAXED
# ---------------------------------------------------------------------------
def test_cross_file_reference(run_dir):
    out = os.path.join(run_dir, "cross_file.gdx")
    with GdxFile() as foreign:
        foreign.append(GdxSymbol("foreign_a", GamsDataType.Set, dims=["a"]))
        foreign[-1].dataframe = _set_df(["a1"], col="a")
        with GdxFile() as gdx:
            gdx.append(GdxSymbol("local_only", GamsDataType.Set, dims=["a"]))
            gdx[-1].dataframe = _set_df(["a1"], col="a")
            # Point child's domain at a symbol that lives in the foreign file.
            gdx["local_only"]._domain = [foreign["foreign_a"]]
            gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        # foreign_a wasn't in this file, so we fell back to relaxed.
        # The on-disk relaxed domain comes from self.dims at write time, which
        # was 'a' (we patched _domain directly, bypassing the coupling setter).
        assert gdx["local_only"].domain_type == GamsDomainType.RELAXED
        assert gdx["local_only"].dims == ["a"]


# ---------------------------------------------------------------------------
# 10. Replaced parent (same name, different identity) -> strict succeeds
# ---------------------------------------------------------------------------
def test_replaced_parent(run_dir):
    out = os.path.join(run_dir, "replaced_parent.gdx")
    with _make_parent_child() as gdx:
        # Build a brand-new symbol with the same name as the parent.
        new_parent = GdxSymbol("t", GamsDataType.Set, dims=["t"])
        new_parent.dataframe = _set_df(["a", "b", "c", "d"], col="t")
        # Replace by index.
        gdx[0] = new_parent
        assert gdx["t"] is new_parent
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        # Lookup is by name; replacement is transparent at write time.
        assert gdx["sub_t"].domain_type == GamsDomainType.REGULAR


# ---------------------------------------------------------------------------
# 11. Out-of-order write + reorder_for_strict_domains
# ---------------------------------------------------------------------------
def test_out_of_order_then_reorder(run_dir):
    out1 = os.path.join(run_dir, "out_of_order1.gdx")
    out2 = os.path.join(run_dir, "out_of_order2.gdx")
    with GdxFile() as gdx:
        # Append child BEFORE parent.
        gdx.append(GdxSymbol("sub_t", GamsDataType.Set, dims=["t"]))
        gdx[-1].dataframe = _set_df(["a", "c"], col="t")
        gdx.append(GdxSymbol("t", GamsDataType.Set, dims=["t"]))
        gdx[-1].dataframe = _set_df(["a", "b", "c"], col="t")
        # Now hook up the strict ref retroactively.
        gdx["sub_t"].domain = [gdx["t"]]
        gdx.write(out1)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out1)
        # Parent wasn't yet in the symbol table when child's write began,
        # so strict was unavailable; relaxed is the fallback.
        assert gdx["sub_t"].domain_type == GamsDomainType.RELAXED

    # Now reorder and retry.
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("sub_t", GamsDataType.Set, dims=["t"]))
        gdx[-1].dataframe = _set_df(["a", "c"], col="t")
        gdx.append(GdxSymbol("t", GamsDataType.Set, dims=["t"]))
        gdx[-1].dataframe = _set_df(["a", "b", "c"], col="t")
        gdx["sub_t"].domain = [gdx["t"]]
        gdx.reorder_for_strict_domains()
        assert [s.name for s in gdx] == ["t", "sub_t"]
        gdx.write(out2)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out2)
        assert gdx["sub_t"].domain_type == GamsDomainType.REGULAR


# ---------------------------------------------------------------------------
# 12. Backward compatibility: dims-only code produces RELAXED as before
# ---------------------------------------------------------------------------
def test_backward_compatibility_dims_only(run_dir):
    out = os.path.join(run_dir, "back_compat.gdx")
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("a", GamsDataType.Set, dims=["a"]))
        gdx[-1].dataframe = _set_df(["a1", "a2"], col="a")
        # No domain assignment, no GdxSymbol refs — pure pre-existing API.
        gdx.append(GdxSymbol("b", GamsDataType.Set, dims=["a"]))
        gdx[-1].dataframe = _set_df(["a1"], col="a")
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        # 'b' has a string dim that names 'a' but no GdxSymbol ref; strict not triggered.
        assert gdx["b"].domain_type == GamsDomainType.RELAXED
        assert gdx["b"].domain is None
        assert gdx["b"].dims == ["a"]


# ---------------------------------------------------------------------------
# 13. dims continues to mutate column headers
# ---------------------------------------------------------------------------
def test_dims_mutates_columns():
    sym = GdxSymbol("s", GamsDataType.Set, dims=["old"])
    sym.dataframe = _set_df(["x", "y"], col="old")
    sym.dims = ["new"]
    assert sym.dataframe.columns[0] == "new"
    assert sym.dims == ["new"]


# ---------------------------------------------------------------------------
# 14. Setting dims clears _domain
# ---------------------------------------------------------------------------
def test_setting_dims_clears_domain():
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("a", GamsDataType.Set, dims=["a"]))
        gdx[-1].dataframe = _set_df(["a1"], col="a")

        gdx.append(GdxSymbol("b", GamsDataType.Set, dims=["a"], domain=[gdx["a"]]))
        gdx[-1].dataframe = _set_df(["a1"], col="a")
        assert gdx["b"].domain_type == GamsDomainType.REGULAR

        gdx["b"].dims = ["a"]
        assert gdx["b"].domain is None
        assert gdx["b"].domain_type == GamsDomainType.RELAXED


# ---------------------------------------------------------------------------
# 15. Read strict GDX from a known-good fixture (skipped if not present)
# ---------------------------------------------------------------------------
def test_read_external_strict_fixture(base_dir):
    fixture = os.path.join(base_dir, "strict_domain_fixture.gdx")
    if not os.path.exists(fixture):
        pytest.skip(
            "tests/strict_domain_fixture.gdx not present; "
            "run dev/build_strict_domain_fixture.py to generate it."
        )
    with GdxFile(lazy_load=False) as gdx:
        gdx.read(fixture)
        assert "t" in gdx and "sub_t" in gdx
        assert gdx["sub_t"].domain_type == GamsDomainType.REGULAR
        assert gdx["sub_t"].domain is not None
        assert gdx["sub_t"].domain[0] is gdx["t"]


# ---------------------------------------------------------------------------
# 16. Lazy load: accessing domain doesn't materialize parent's dataframe
# ---------------------------------------------------------------------------
def test_lazy_load_domain_access(run_dir):
    out = os.path.join(run_dir, "lazy_domain.gdx")
    with _make_parent_child() as gdx:
        gdx.write(out)

    with GdxFile(lazy_load=True) as gdx:
        gdx.read(out)
        # No symbol loaded yet.
        assert not gdx["t"].loaded
        assert not gdx["sub_t"].loaded
        sub = gdx["sub_t"]
        # Touching .domain reads metadata only — should not load the parent.
        _ = sub.domain
        assert not gdx["t"].loaded


# ---------------------------------------------------------------------------
# 17. to_gdx(..., domains=) produces REGULAR
# ---------------------------------------------------------------------------
def test_to_gdx_with_domains(run_dir):
    out = os.path.join(run_dir, "to_gdx_domains.gdx")
    dataframes = {
        "a": pd.DataFrame([["a1", True], ["a2", True]], columns=["a", "Value"]),
        "sub_a": pd.DataFrame([["a1", True]], columns=["a", "Value"]),
    }
    gdxpds.to_gdx(dataframes, out, domains={"sub_a": ["a"]})

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        assert gdx["sub_a"].domain_type == GamsDomainType.REGULAR
        assert gdx["sub_a"].domain is not None
        assert gdx["sub_a"].domain[0] is gdx["a"]


# ---------------------------------------------------------------------------
# 18. to_gdx with unknown parent in domains raises
# ---------------------------------------------------------------------------
def test_to_gdx_unknown_parent_raises():
    dataframes = {
        "sub_a": pd.DataFrame([["a1", True]], columns=["a", "Value"]),
    }
    with pytest.raises(DomainError):
        gdxpds.to_gdx(dataframes, domains={"sub_a": ["nonexistent_parent"]})


# ---------------------------------------------------------------------------
# 19. get_subset_relationships
# ---------------------------------------------------------------------------
def test_get_subset_relationships(run_dir):
    out = os.path.join(run_dir, "for_get_subset_rels.gdx")
    with _make_parent_child() as gdx:
        gdx.write(out)

    rels = gdxpds.get_subset_relationships(out)
    assert set(rels.keys()) == {"t", "sub_t"}
    # 't' was built with dims=['t'] — its recorded domain is the literal name
    # 't' (self-referential), reported verbatim rather than collapsed to None,
    # so the value round-trips back through to_gdx(domains=...).
    assert rels["t"] == ["t"]
    # 'sub_t' is a genuine subset of 't'.
    assert rels["sub_t"] == ["t"]


# ---------------------------------------------------------------------------
# 20. clone() preserves refs
# ---------------------------------------------------------------------------
def test_clone_preserves_refs(run_dir):
    out = os.path.join(run_dir, "clone_preserves.gdx")
    with _make_parent_child() as gdx:
        clone_parent = gdx["t"].clone()
        clone_child = gdx["sub_t"].clone()
        assert clone_child._domain is not None
        # The clone still references the ORIGINAL parent (cheaply, by identity).
        assert clone_child._domain[0] is gdx["t"]

    # Re-rooting the clones in a fresh file should let strict survive via name.
    with GdxFile() as gdx2:
        gdx2.append(clone_parent)
        gdx2.append(clone_child)
        gdx2.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        assert gdx["sub_t"].domain_type == GamsDomainType.REGULAR
        assert gdx["sub_t"].domain is not None
        assert gdx["sub_t"].domain[0] is gdx["t"]


# ---------------------------------------------------------------------------
# 21. get_subset_relationships output round-trips through to_gdx(domains=...)
# ---------------------------------------------------------------------------
def test_get_subset_relationships_round_trips_through_to_gdx(run_dir):
    out1 = os.path.join(run_dir, "rels_rt_1.gdx")
    out2 = os.path.join(run_dir, "rels_rt_2.gdx")
    dataframes = {
        "a":     pd.DataFrame([["a1", True], ["a2", True], ["a3", True]],
                              columns=["a", "Value"]),
        "sub_a": pd.DataFrame([["a1", True], ["a3", True]],
                              columns=["a", "Value"]),
        "free":  pd.DataFrame([["x1", True]], columns=["*", "Value"]),
    }
    # Build with a genuine subset (sub_a -> a) and a wildcard set (free).
    gdxpds.to_gdx(dataframes, out1, domains={"sub_a": ["a"], "free": [None]})

    rels = gdxpds.get_subset_relationships(out1)
    # Covers all three reported forms: genuine parent, self-referential root
    # (reported verbatim), and wildcard (None).
    assert rels["a"] == ["a"]
    assert rels["sub_a"] == ["a"]
    assert rels["free"] == [None]

    # Feed the read relationships straight back in; the shape must be accepted
    # and the resulting file must report identical relationships.
    gdxpds.to_gdx(dataframes, out2, domains=rels)
    assert gdxpds.get_subset_relationships(out2) == rels
