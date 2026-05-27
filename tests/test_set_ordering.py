"""Pins GDX's Set-element ordering semantics on round-trip (GitHub issue #75).

Issue #75 (2020) reported that ``to_gdx`` reordered Set elements on write. The
exact example from the issue -- a Set with elements ``['2008', '2010', '2015',
'2020']`` written in isolation -- DOES round-trip in insertion order on both
engines today (test_issue_75_isolated_set_preserves_order below).

The 2020 report's actual situation was a 198-symbol file. The reorder is a
property of GAMS's GDX file format, not of either engine: GDX stores a Set's
records sorted by the global UEL-pool index, so the first symbol to introduce
a UEL fixes its position for every later symbol that references it. We confirm
this is GDX-level (not gdxpds-level) two ways:

1. The same reorder reproduces under ``gams_transfer`` -> ``gams_transfer``,
   which never touches our ``gdxDataWriteStr`` write loop.
2. GAMS's own ``gdxdump`` tool prints the file in the reordered order, so the
   bytes on disk are already sorted by the time any reader sees them.

The first test pins the no-collision case (issue #75's literal example). The
second test pins the UEL-collision case as the documented semantic, with the
workaround spelled out: write the order-sensitive Set first."""

import os

import pandas as pd
import pytest

import gdxpds
from gdxpds import to_dataframes, to_gdx

_ENGINES = ["gdxcc"] + (["gams_transfer"] if gdxpds.HAVE_GAMS_TRANSFER else [])


@pytest.mark.parametrize("write_engine", _ENGINES)
@pytest.mark.parametrize("read_engine", _ENGINES)
def test_issue_75_isolated_set_preserves_order(tmp_path, write_engine, read_engine):
    # Issue #75's literal example: a single Set with year-like string elements
    # in non-lexicographic order. With no UEL pre-population by another symbol,
    # the order survives unchanged on every (write, read) engine combination.
    elements = ["2008", "2010", "2015", "2020"]
    dfs = {"years": pd.DataFrame({"i": elements, "Value": [True] * len(elements)})}

    out = os.path.join(tmp_path, f"years_{write_engine}.gdx")
    to_gdx(dfs, out, engine=write_engine)
    back = to_dataframes(out, engine=read_engine)

    assert list(back) == ["years"]
    assert back["years"].iloc[:, 0].tolist() == elements


@pytest.mark.parametrize("write_engine", _ENGINES)
@pytest.mark.parametrize("read_engine", _ENGINES)
def test_uel_collision_reorders_to_uel_pool_order(tmp_path, write_engine, read_engine):
    # GDX file-format semantic: a Set's records are stored in UEL-pool index
    # order, which is fixed by the first symbol to introduce each UEL. Here
    # ``leading`` registers 2010, 2015, 2020 first; ``years`` then introduces
    # 2008. On read, ``years`` appears as [2010, 2015, 2020, 2008] -- 2008
    # moves to the end because it was registered last. This holds on both
    # engines including ``gams_transfer`` <-> ``gams_transfer``, which never
    # touches gdxpds's ``gdxDataWriteStr`` loop, so the reorder is a property
    # of GDX itself (verified independently with ``gdxdump``).
    leading = ["2010", "2015", "2020"]
    years_input = ["2008", "2010", "2015", "2020"]
    dfs = {
        "leading": pd.DataFrame({"i": leading, "Value": [True] * len(leading)}),
        "years": pd.DataFrame({"i": years_input, "Value": [True] * len(years_input)}),
    }

    out = os.path.join(tmp_path, f"prepop_{write_engine}.gdx")
    to_gdx(dfs, out, engine=write_engine)
    back = to_dataframes(out, engine=read_engine)

    assert back["leading"].iloc[:, 0].tolist() == leading
    assert back["years"].iloc[:, 0].tolist() == ["2010", "2015", "2020", "2008"]


@pytest.mark.parametrize("write_engine", _ENGINES)
@pytest.mark.parametrize("read_engine", _ENGINES)
def test_uel_collision_workaround_write_order_sensitive_set_first(
    tmp_path, write_engine, read_engine
):
    # Documented workaround for the semantic in
    # test_uel_collision_reorders_to_uel_pool_order: write the order-sensitive
    # Set first, so its order fixes the UEL-pool index for every later symbol
    # that references the same elements. Same dictionary contents as the
    # collision test, swapped insertion order; ``years`` now reads back in
    # input order.
    years_input = ["2008", "2010", "2015", "2020"]
    leading = ["2010", "2015", "2020"]
    dfs = {
        "years": pd.DataFrame({"i": years_input, "Value": [True] * len(years_input)}),
        "leading": pd.DataFrame({"i": leading, "Value": [True] * len(leading)}),
    }

    out = os.path.join(tmp_path, f"workaround_{write_engine}.gdx")
    to_gdx(dfs, out, engine=write_engine)
    back = to_dataframes(out, engine=read_engine)

    assert back["years"].iloc[:, 0].tolist() == years_input
    assert back["leading"].iloc[:, 0].tolist() == leading
