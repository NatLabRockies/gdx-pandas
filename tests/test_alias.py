"""Alias data-model and write-API behavior: the aliased_with reference, the
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


def test_aliased_with_setter_rejects_string():
    # Mirrors domain.setter: a parent must be a GdxSymbol reference, not a name.
    with pytest.raises(DomainError):
        GdxSymbol("at", GamsDataType.Alias, aliased_with="t")


def test_non_alias_has_no_aliased_with():
    s = GdxSymbol("s", GamsDataType.Set, dims=["i"])
    assert s.aliased_with is None
    assert s.aliased_with_name is None


def test_append_alias_builds_alias(run_dir):
    out = os.path.join(run_dir, "append_alias.gdx")
    with GdxFile() as gdx:
        parent = append_set(gdx, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
        at = append_alias(gdx, "at", parent)
        assert at.data_type == GamsDataType.Alias
        assert at.aliased_with is parent
        gdx.write(out)

    with GdxFile(lazy_load=False) as gdx:
        gdx.read(out)
        assert gdx["at"].data_type == GamsDataType.Alias
        assert gdx["at"].aliased_with is gdx["t"]


def test_append_alias_by_name(run_dir):
    out = os.path.join(run_dir, "append_alias_by_name.gdx")
    with GdxFile() as gdx:
        append_set(gdx, "t", pd.DataFrame({"i": ["a", "b"]}))
        at = append_alias(gdx, "at", "t")  # parent given by name
        assert at.aliased_with is gdx["t"]
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
    # A universe alias (alias of '*', not a named Set) reads with aliased_with
    # resolved to the file's universal_set, and round-trips on both backends.
    src = os.path.join(data_dir, "universe_alias_fixture.gdx")
    backends = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for be in backends:
        with GdxFile(lazy_load=False, backend=be) as f:
            f.read(src)
            u = f["u"]
            assert u.data_type == GamsDataType.Alias
            assert u.aliased_with is f.universal_set
            assert u.aliased_with.name == "*"
            out = str(tmp_path / f"rt_{be}.gdx")
            f.clone().write(out)
        with GdxFile(lazy_load=False, backend=be) as g:
            g.read(out)
            assert g["u"].data_type == GamsDataType.Alias
            assert g["u"].aliased_with is g.universal_set


def test_to_gdx_aliases_name_collision_raises():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a"], "Value": [""]})},
            aliases={"t": "t"},  # alias name collides with an existing symbol
        )


def test_aliased_with_setter_rejects_on_non_alias_symbol():
    # Fail fast on the data model: aliased_with only makes sense on an Alias.
    s = GdxSymbol("s", GamsDataType.Set, dims=["i"])
    parent = GdxSymbol("t", GamsDataType.Set, dims=["i"])
    with pytest.raises(DomainError):
        s.aliased_with = parent


def test_aliased_with_setter_rejects_non_set_parent():
    # Fail fast on the data model: parent must be a Set or another Alias.
    a = GdxSymbol("at", GamsDataType.Alias, dims=["i"])
    p = GdxSymbol("p", GamsDataType.Parameter, dims=["i"])
    with pytest.raises(DomainError):
        a.aliased_with = p


def test_aliased_with_setter_accepts_alias_parent():
    # Chained aliases (alias of an alias) are allowed by both backends; the gdxcc
    # backend preserves the chain on write, gams_transfer flattens it to the root.
    parent_set = GdxSymbol("t", GamsDataType.Set, dims=["i"])
    a1 = GdxSymbol("at", GamsDataType.Alias, dims=["i"], aliased_with=parent_set)
    a2 = GdxSymbol("aat", GamsDataType.Alias, dims=["i"], aliased_with=a1)
    assert a2.aliased_with is a1
    assert a2.aliased_with_name == "at"


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
        assert cloned_at.aliased_with is None
        assert cloned_at.aliased_with_name == "t"
        # And it can be inserted into a fresh file and resolved there.
        with GdxFile() as dest:
            append_set(dest, "t", pd.DataFrame({"i": ["x", "y"]}))
            dest.append(cloned_at)
            cloned_at.resolve_aliased_with()
            assert cloned_at.aliased_with is dest["t"]
            dest.write(out)


def test_alias_of_alias_roundtrip_both_backends(run_dir):
    # End-to-end behavior: both backends accept alias-of-alias on write and read
    # it back resolved to a same-file symbol. gdxcc preserves the chain on disk
    # (aat -> at -> t); gams_transfer flattens to the root (aat -> t). Either is
    # acceptable; the contract is that aliased_with always resolves to a same-file
    # parent and aliased_with_name is the resolved parent's name.
    backends = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])
    for be in backends:
        out = os.path.join(run_dir, f"aoa_{be}.gdx")
        with GdxFile(backend=be) as f:
            t = append_set(f, "t", pd.DataFrame({"i": ["a", "b", "c"]}))
            at = append_alias(f, "at", t)
            append_alias(f, "aat", at)  # alias of an alias
            f.write(out)
        with GdxFile(lazy_load=False, backend=be) as g:
            g.read(out)
            assert g["aat"].data_type == GamsDataType.Alias
            assert g["aat"].aliased_with is not None
            assert g["aat"].aliased_with_name in ("at", "t")
