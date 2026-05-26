"""Alias data-model and write-API behavior: the alias_of reference, the
append_alias / to_gdx(aliases=) builders, and the no-relaxed-fallback rule that
an unknown or non-Set parent raises DomainError."""

import os

import pandas as pd
import pytest

import gdxpds
from gdxpds.gdx import (
    DomainError,
    GamsDataType,
    GdxFile,
    GdxSymbol,
    append_alias,
    append_set,
)
from gdxpds.tools import Error


def test_alias_of_setter_rejects_string():
    # Mirrors domain.setter: a parent must be a GdxSymbol reference, not a name.
    with pytest.raises(DomainError):
        GdxSymbol("at", GamsDataType.Alias, alias_of="t")


def test_non_alias_has_no_alias_of():
    s = GdxSymbol("s", GamsDataType.Set, dims=["i"])
    assert s.alias_of is None
    assert s.alias_of_name is None


def test_append_alias_builds_alias(run_dir):
    out = os.path.join(run_dir, "append_alias.gdx")
    with GdxFile() as gdx:
        parent = append_set(gdx, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
        at = append_alias(gdx, "at", parent)
        assert at.data_type == GamsDataType.Alias
        assert at.alias_of is parent
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        assert gdx["at"].data_type == GamsDataType.Alias
        assert gdx["at"].alias_of is gdx["t"]


def test_append_alias_by_name(run_dir):
    out = os.path.join(run_dir, "append_alias_by_name.gdx")
    with GdxFile() as gdx:
        append_set(gdx, "t", pd.DataFrame({"i": ["a", "b"]}))
        at = append_alias(gdx, "at", "t")  # parent given by name
        assert at.alias_of is gdx["t"]
        gdx.write(out)


def test_append_alias_unknown_parent_raises():
    with GdxFile() as gdx:
        with pytest.raises(DomainError):
            append_alias(gdx, "at", "nope")


def test_append_alias_non_set_parent_raises():
    with GdxFile() as gdx:
        gdx.append(GdxSymbol("p", GamsDataType.Parameter, dims=["i"]))
        gdx["p"].dataframe = pd.DataFrame({"i": ["a"], "Value": [1.0]})
        with pytest.raises(DomainError):
            append_alias(gdx, "ap", "p")


def test_to_gdx_aliases_unknown_parent_raises():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a"], "Value": [""]})},
            aliases={"at": "missing"},
        )


def test_to_gdx_aliases_non_set_parent_raises():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"p": pd.DataFrame({"i": ["a"], "Value": [1.0]})},
            aliases={"ap": "p"},
        )


def test_universe_alias_reads_and_roundtrips(data_dir, tmp_path):
    # A universe alias (alias of '*', not a named Set) reads with alias_of
    # resolved to the file's universal_set, and round-trips on both engines.
    src = os.path.join(data_dir, "universe_alias_fixture.gdx")
    engines = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for engine in engines:
        with GdxFile(lazy_load=False, engine=engine) as f:
            f.read(src)
            u = f["u"]
            assert u.data_type == GamsDataType.Alias
            assert u.alias_of is f.universal_set
            assert u.alias_of.name == "*"
            out = str(tmp_path / f"rt_{engine}.gdx")
            f.clone().write(out)
        with GdxFile(lazy_load=False, engine=engine) as g:
            g.read(out)
            assert g["u"].data_type == GamsDataType.Alias
            assert g["u"].alias_of is g.universal_set


def test_to_gdx_aliases_name_collision_raises():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a"], "Value": [""]})},
            aliases={"t": "t"},  # alias name collides with an existing symbol
        )


def test_get_aliases_round_trip(run_dir):
    # Companion to get_subset_relationships: the high-level reader surfaces alias
    # parent relationships through get_aliases, output-shape-compatible with the
    # aliases= argument of to_gdx so the relationship can be round-tripped.
    out = os.path.join(run_dir, "get_aliases.gdx")
    gdxpds.to_gdx(
        {"t": pd.DataFrame({"i": ["a", "b", "c"], "Value": ["", "", ""]})},
        out,
        aliases={"at": "t"},
    )
    aliases = gdxpds.get_aliases(out)
    assert aliases == {"at": "t"}
    # Re-feeding the output into to_gdx() reproduces the same alias set.
    out2 = os.path.join(run_dir, "get_aliases_rt.gdx")
    gdxpds.to_gdx(
        {"t": pd.DataFrame({"i": ["a", "b", "c"], "Value": ["", "", ""]})},
        out2,
        aliases=aliases,
    )
    assert gdxpds.get_aliases(out2) == {"at": "t"}


def test_get_aliases_empty_when_no_aliases(run_dir):
    # Only Alias symbols appear in the output -- a file with no aliases yields {}.
    out = os.path.join(run_dir, "no_aliases.gdx")
    gdxpds.to_gdx({"t": pd.DataFrame({"i": ["a"], "Value": [""]})}, out)
    assert gdxpds.get_aliases(out) == {}


def test_alias_of_setter_rejects_on_non_alias_symbol():
    # Fail fast on the data model: alias_of only makes sense on an Alias.
    s = GdxSymbol("s", GamsDataType.Set, dims=["i"])
    parent = GdxSymbol("t", GamsDataType.Set, dims=["i"])
    with pytest.raises(DomainError):
        s.alias_of = parent


def test_alias_of_setter_rejects_non_set_parent():
    # Fail fast on the data model: parent must be a Set or another Alias.
    a = GdxSymbol("at", GamsDataType.Alias, dims=["i"])
    p = GdxSymbol("p", GamsDataType.Parameter, dims=["i"])
    with pytest.raises(DomainError):
        a.alias_of = p


def test_alias_of_setter_accepts_alias_parent():
    # Chained aliases (alias of an alias) are allowed by both engines; the gdxcc
    # engine preserves the chain on write, gams_transfer flattens it to the root.
    parent_set = GdxSymbol("t", GamsDataType.Set, dims=["i"])
    a1 = GdxSymbol("at", GamsDataType.Alias, dims=["i"], alias_of=parent_set)
    a2 = GdxSymbol("aat", GamsDataType.Alias, dims=["i"], alias_of=a1)
    assert a2.alias_of is a1
    assert a2.alias_of_name == "at"


def test_to_gdx_aliases_rejects_non_str_key():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a"], "Value": [""]})},
            aliases={42: "t"},  # non-str alias name
        )


def test_clone_alias_drops_live_parent_ref(run_dir):
    # Cloning an alias must not carry the live parent ref from the source file;
    # only the parent name survives, and is re-resolved against the destination.
    out = os.path.join(run_dir, "clone_alias.gdx")
    with GdxFile() as src:
        append_set(src, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
        append_alias(src, "at", "t")
        cloned_at = src["at"].clone()
        # The clone retains the parent name but not a live ref to src["t"].
        assert cloned_at.alias_of is None
        assert cloned_at.alias_of_name == "t"
        # And it can be inserted into a fresh file and resolved there.
        with GdxFile() as dest:
            append_set(dest, "t", pd.DataFrame({"i": ["x", "y"]}))
            dest.append(cloned_at)
            cloned_at.resolve_alias_of()
            assert cloned_at.alias_of is dest["t"]
            dest.write(out)


def test_alias_of_alias_roundtrip_both_engines(run_dir):
    # End-to-end behavior: both engines accept alias-of-alias on write and read
    # it back resolved to a same-file symbol. They differ in what reaches disk.
    engines = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for engine in engines:
        out = os.path.join(run_dir, f"aoa_{engine}.gdx")
        with GdxFile(engine=engine) as f:
            t = append_set(f, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
            at = append_alias(f, "at", t)
            append_alias(f, "aat", at)  # alias of an alias
            f.write(out)
        with GdxFile(lazy_load=False, engine=engine) as g:
            g.read(out)
            assert g["aat"].data_type == GamsDataType.Alias
            assert g["aat"].alias_of is not None
            # gdxcc preserves the chain on disk (aat -> at); gams_transfer flattens
            # to the root Set (aat -> t). Either way `alias_of` resolves to a
            # same-file ref. This pair of asserts locks in the asymmetry so a
            # engine-level behavior change is caught instead of silently absorbed.
            expected_parent = "at" if engine == "gdxcc" else "t"
            assert g["aat"].alias_of_name == expected_parent
            assert g["aat"].alias_of is g[expected_parent]


@pytest.mark.skipif(not gdxpds.HAVE_GAMS_TRANSFER, reason="gams.transfer not available")
def test_alias_chain_disk_shape_differs_between_engines(run_dir):
    # Stronger version of the above: read the raw on-disk GDX through gdxcc to
    # show that the two engines really do produce different files for the same
    # in-memory model. gdxcc records `aat`'s parent at the alias's userinfo
    # symbol-index (here index 2, == `at`); gams_transfer collapses it to the
    # root Set's index (here index 1, == `t`).
    from gams.core import gdx as gdxcc

    from gdxpds.tools import GamsDirFinder, _GdxHandle, load_gdxcc

    gdir = GamsDirFinder().gams_dir
    load_gdxcc(gams_dir=gdir)

    def aat_parent_name_on_disk(path):
        with _GdxHandle(gdxcc, gdir, None) as h:
            H = h.H
            assert gdxcc.gdxOpenRead(H, path)[0]
            _, scount, _ = gdxcc.gdxSystemInfo(H)
            for i in range(scount + 1):
                _, name, _, dt = gdxcc.gdxSymbolInfo(H, i)
                _, _, userinfo, _ = gdxcc.gdxSymbolInfoX(H, i)
                if name == "aat":
                    assert dt == gdxcc.GMS_DT_ALIAS
                    _, parent_name, _, _ = gdxcc.gdxSymbolInfo(H, userinfo)
                    return parent_name
        raise AssertionError("aat not found in GDX")

    for engine, expected in [("gdxcc", "at"), ("gams_transfer", "t")]:
        out = os.path.join(run_dir, f"aoa_disk_{engine}.gdx")
        with GdxFile(engine=engine) as f:
            t = append_set(f, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
            at = append_alias(f, "at", t)
            append_alias(f, "aat", at)
            f.write(out)
        assert aat_parent_name_on_disk(out) == expected, (
            f"engine {engine!r}: expected on-disk `aat` parent={expected!r}, "
            f"got {aat_parent_name_on_disk(out)!r}"
        )


# --- Alias.dataframe is a read-only view onto the parent ----------------------


def test_alias_dataframe_is_view_of_parent_in_memory():
    # An alias carries no records of its own; its `.dataframe` is the parent's
    # `.dataframe` (same object, not a copy).
    with GdxFile() as gdx:
        t = append_set(gdx, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
        at = append_alias(gdx, "at", t)
        assert at.dataframe is t.dataframe


def test_alias_dataframe_view_reflects_parent_mutation():
    # Mutating the parent's `.dataframe` shows through the alias immediately:
    # there's no per-alias copy that could drift.
    with GdxFile() as gdx:
        t = append_set(gdx, "t", pd.DataFrame({"i": ["a", "b"]}))
        at = append_alias(gdx, "at", t)
        t.dataframe = pd.DataFrame({"i": ["x", "y", "z"]})
        assert at.dataframe is t.dataframe
        assert list(at.dataframe["i"]) == ["x", "y", "z"]


def test_alias_dataframe_direct_assignment_raises():
    # Setting `alias.dataframe` directly is not allowed -- the alias has no
    # records of its own. Mutate the parent instead.
    with GdxFile() as gdx:
        append_set(gdx, "t", pd.DataFrame({"i": ["a", "b"]}))
        at = append_alias(gdx, "at", "t")
        with pytest.raises(Error):
            at.dataframe = pd.DataFrame({"i": ["x"], "Value": [""]})


def test_alias_unload_does_not_assign_dataframe():
    # unload() on an alias must not go through the dataframe setter (which
    # would raise); it just flips the loaded flag.
    with GdxFile() as gdx:
        append_set(gdx, "t", pd.DataFrame({"i": ["a", "b"]}))
        at = append_alias(gdx, "at", "t")
        assert at.loaded
        at.unload()
        assert not at.loaded


def test_alias_num_records_tracks_parent():
    # num_records uses `self.dataframe` for loaded symbols. Since an alias's
    # dataframe is a view of the parent, its count matches the parent's.
    with GdxFile() as gdx:
        t = append_set(gdx, "t", pd.DataFrame({"i": ["a", "b", "c", "d"]}))
        at = append_alias(gdx, "at", t)
        assert at.num_records == t.num_records == 4


def test_alias_dataframe_view_after_read(run_dir):
    # After reading the file back, `at.dataframe is t.dataframe` holds: the
    # gdxcc and gams.transfer engines skip the alias's own dataframe
    # assignment and rely on the view.
    out = os.path.join(run_dir, "alias_view_read.gdx")
    gdxpds.to_gdx(
        {"t": pd.DataFrame({"i": ["a", "b", "c"], "Value": ["", "", ""]})},
        out,
        aliases={"at": "t"},
    )
    engines = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for engine in engines:
        with GdxFile(lazy_load=False, engine=engine) as gdx:
            gdx.read(out)
            assert gdx["at"].dataframe is gdx["t"].dataframe


def test_alias_lazy_load_pulls_parent_via_view(run_dir):
    # With lazy loading, accessing the alias's records via .load() ensures the
    # parent is loaded too -- the view on an unloaded parent would be empty.
    engines = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for engine in engines:
        out = os.path.join(run_dir, f"alias_lazy_{engine}.gdx")
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a", "b", "c"], "Value": ["", "", ""]})},
            out,
            aliases={"at": "t"},
            engine=engine,
        )
        with GdxFile(lazy_load=True, engine=engine) as gdx:
            gdx.read(out)
            assert not gdx["t"].loaded
            assert not gdx["at"].loaded
            gdx["at"].load()
            # Loading the alias must also have loaded the parent, so the
            # view actually surfaces the members.
            assert gdx["t"].loaded
            assert gdx["at"].loaded
            assert list(gdx["at"].dataframe.iloc[:, 0]) == ["a", "b", "c"]


def test_clone_alias_dataframe_resolves_via_parent_in_destination(run_dir):
    # A cloned alias has no live `alias_of` ref until re-resolved; once it is,
    # its `.dataframe` view points at the *destination* file's parent Set,
    # which can be a different set of members than the source file's.
    out = os.path.join(run_dir, "clone_alias_view.gdx")
    with GdxFile() as src:
        append_set(src, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
        append_alias(src, "at", "t")
        cloned_at = src["at"].clone()
        with GdxFile() as dest:
            append_set(dest, "t", pd.DataFrame({"i": ["x", "y"]}))
            dest.append(cloned_at)
            cloned_at.resolve_alias_of()
            # The view picks up the destination file's parent records.
            assert cloned_at.dataframe is dest["t"].dataframe
            assert list(cloned_at.dataframe["i"]) == ["x", "y"]
            dest.write(out)
