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


def test_to_gdx_aliases_name_collision_raises():
    with pytest.raises(DomainError):
        gdxpds.to_gdx(
            {"t": pd.DataFrame({"i": ["a"], "Value": [""]})},
            aliases={"t": "t"},  # alias name collides with an existing symbol
        )
