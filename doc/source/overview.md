# Overview

There are two main ways to use gdxpds. The first use case is the one that was initially supported: direct conversion between GDX files on disk and pandas DataFrames or a csv version thereof. Starting with the Version 1.0.0 rewrite, there is now a second style of use which involves interfacing with GDX files and symbols via the {py:class}`gdxpds.gdx.GdxFile` and {py:class}`gdxpds.gdx.GdxSymbol` classes. Either way, [Configuration](#configuration) — where to find GAMS and which I/O engine to use — works the same.

[Direct Conversion](#direct-conversion) | [Object-Oriented API](#object-oriented-api) | [Configuration](#configuration)

## Direct Conversion

The two primary points of reference for the direct conversion utilities are GDX files on disk and Python dicts of `{symbol_name: pandas.DataFrame}`, where each `pandas.DataFrame` contains data for a single set, parameter, equation, variable, or alias. The shape of the value columns depends on the symbol {py:data}`gdxpds.gdx.GamsDataType`:

- **Sets and Aliases** have a single `Value` column whose entries are the GAMS *element text* string, with `""` denoting a member that has no text. Membership is conveyed by **row presence**: every row is a member. On write, the value column can be omitted, given as booleans (any value whether `True` or `False` means "member, no text"), or given as text strings.
- **Parameters** have a single `Value` column of type `float`; the GAMS specials `NA`, `UNDEF`, `+Inf` / `-Inf`, and `EPS` map to `numpy.nan`, `None`, `numpy.inf` / `-numpy.inf`, and `numpy.finfo(float).tiny` (see [Special values](#special-values)).
- **Variables and Equations** have five value columns — level, marginal, lower, upper, scale — as enumerated in {py:class}`gdxpds.gdx.GamsValueType`; see {py:data}`gdxpds.gdx.GAMS_VALUE_COLS_MAP`.

Aliases reuse their parent's records (on read they look like the Set they alias) and their parent relationship is carried separately; see [Aliases](#aliases) and the `aliases=` example below.

The basic interface to convert from GDX to DataFrames is {py:func}`gdxpds.to_dataframes`:

```python
import gdxpds

gdx_file = 'C:\\path_to_my_gdx\\data.gdx'
dataframes = gdxpds.to_dataframes(gdx_file)
for symbol_name, df in dataframes.items():
    print(f"Doing work with {symbol_name}\n{df}.")
```

And vice-versa we have {py:func}`gdxpds.to_gdx`:

```python
import gdxpds

# assume we have a DataFrame df with last column 'value'
data_ready_for_GAMS = { 'symbol_name': df }

gdx_file = 'C:\\path_to_my_output_gdx\\data_to_send_to_gams.gdx'
gdx = gdxpds.to_gdx(data_ready_for_GAMS, gdx_file)
```

Note that providing a `gdx_file` path is optional. In either case the in-memory gdx file is returned as an object of type {py:class}`gdxpds.gdx.GdxFile`.

Additional functions include:

- {py:func}`gdxpds.list_symbols`
- {py:func}`gdxpds.get_data_types`
- {py:func}`gdxpds.to_dataframe` — returns the named symbol's data as a plain DataFrame.
- {py:func}`gdxpds.get_subset_relationships` — read the domain relationships out of a GDX file, returned as `{symbol_name: [parent_name_or_None_for_wildcard, ...]}`. Any symbol type (Set, Parameter, Variable, Equation) can carry a domain; the parent in each slot is a Set or Alias-of-Set.
- {py:func}`gdxpds.get_aliases` — read the alias relationships out of a GDX file, returned as `{alias_name: parent_name}`. Symbols that are not aliases are not included.

To create a GDX with strict domain relationships from the direct-conversion API, pass a `domains=` mapping to {py:func}`gdxpds.to_gdx`. Any symbol type can carry a domain (a Set's domain is a *subset* relationship; a Parameter/Variable/Equation's is an *indexed-over* relationship); the parent named in each slot must be a Set or Alias-of-Set:

```python
import gdxpds
import pandas as pd

dataframes = {
    'a':     pd.DataFrame([['a1', True], ['a2', True], ['a3', True]], columns=['a', 'Value']),
    'sub_a': pd.DataFrame([['a1', True], ['a3', True]],               columns=['a', 'Value']),
}
gdxpds.to_gdx(dataframes, 'data.gdx', domains={'sub_a': ['a']})

# Read the relationship info back. Domain names are reported verbatim; only
# the wildcard '*' (or a dimension with no recorded domain) comes through as
# None. Here 'a' is a root Set whose single dimension is labeled with its own
# name, so it round-trips as 'a' rather than None.
print(gdxpds.get_subset_relationships('data.gdx'))
# {'a': ['a'], 'sub_a': ['a']}
```

The `domains=` keys are child symbol names; each value is the list of parent Set names (or `None` for the wildcard `'*'`), one entry per dimension. `to_gdx` topologically sorts the input so each parent is written before its children. The Direct Conversion API is **string-based** — parents are named by string. For an object-reference-based API (live links to parent `GdxSymbol`'s, useful when mutating or composing files in Python), see [Domain Relationships](#domain-relationships) under [Object-Oriented API](#object-oriented-api).

Aliases work the same way: pass an `aliases=` mapping (alias name → parent Set name) to {py:func}`gdxpds.to_gdx`, and read the relationships back with {py:func}`gdxpds.get_aliases`:

```python
import gdxpds
import pandas as pd

dataframes = {'t': pd.DataFrame({'i': ['a', 'b', 'c'], 'Value': ['', '', '']})}
gdxpds.to_gdx(dataframes, 'data.gdx', aliases={'at': 't'})

print(gdxpds.get_aliases('data.gdx'))
# {'at': 't'}
```

The package also includes command line utilities for converting between GDX and CSV. After `pip install gdxpds`, these are on `PATH`:

```bash
gdx_to_csv --help
csv_to_gdx --help
```

## Object-Oriented API

The functionality described above can also be acccessed with direct use of the {py:class}`gdxpds.gdx.GdxFile` / {py:class}`gdxpds.gdx.GdxSymbol` object model in {py:mod}`gdxpds.gdx`. To duplicate the GDX read functionality shown above one would write:

```python
import gdxpds

gdx_file = 'C:\\path_to_my_gdx\\data.gdx'
with gdxpds.gdx.GdxFile(lazy_load=False) as f:
    f.read(gdx_file)
    for symbol in f:
        symbol_name = symbol.name
        df = symbol.dataframe
        print(f"Doing work with {symbol_name}:\n{df}")
```

This interface also provides more precise control over what data is loaded at any particular time:

```python
import gdxpds

gdx_file = 'C:\\path_to_my_gdx\\data.gdx'
with gdxpds.gdx.GdxFile() as f:  # lazy_load defaults to True
    f.read(gdx_file)

    f['param_1'].load()
    df_1 = f['param_1'].dataframe
    f['param_1'].unload()

    f['param_12'].load()
    df_12 = f['param_12'].dataframe
    f['param_12'].unload()
```

and enables more transparent creation of new GDX files. The example below exercises a number of features, including some that are documented in more detail in the subsections that follow.

```python
from itertools import product

import numpy as np
import pandas as pd

from gdxpds.gdx import (
    GamsDataType,
    GdxFile,
    GdxSymbol,
    append_alias,
    append_parameter,
    append_set,
)

out_file = 'my_new_gdx_data.gdx'
with GdxFile() as gdx:

    # A Set with element text. The Value column may carry text (string; '' = no
    # text, but the row is still a member). See "Set membership and element text".
    u = append_set(
        gdx, 'u',
        pd.DataFrame({
            'u': [f'u{i}' for i in range(1, 11)],
            'Value': [f'unit {i}' for i in range(1, 11)],
        }),
    )

    # A Set with an explicit strict domain of u (a subset relationship). See
    # "Domain Relationships". The convenience `domain=[u]` reference triggers
    # a strict gdxSymbolSetDomain write; the same mechanism works for a
    # Parameter/Variable/Equation child (indexed-over rather than subset).
    append_set(
        gdx, 'sub_u',
        pd.DataFrame({'u': [f'u{i}' for i in range(1, 6)]}),
        domain=[u],
    )

    # An alias of u -- another name for the same Set. See "Aliases".
    append_alias(gdx, 'au', u)

    # A two-dimensional Set with no element text (just dimension columns).
    v = append_set(gdx, 'v', pd.DataFrame({'v': [f'v{i}' for i in range(1, 6)]}))
    append_set(
        gdx, 'uv',
        pd.DataFrame(
            product([f'u{i}' for i in range(1, 11)], [f'v{i}' for i in range(1, 6)]),
            columns=['u', 'v'],
        ),
        domain=[u, v],
    )

    # A Parameter with one Special value (UNDEF is None on read/write). See
    # "Special values" -- np.nan, np.inf, -np.inf, and np.finfo(float).tiny
    # also round-trip via the same mapping.
    append_parameter(
        gdx, 'p',
        pd.DataFrame({
            'u': [f'u{i}' for i in range(1, 4)],
            'Value': pd.Series([1.5, None, 2.5], dtype=object),  # None = GDX UNDEF
        }),
        domain=[u],
    )

    # Write the file to disk
    gdx.write(out_file)
```

The key classes and functions for the object-oriented API are:

- {py:class}`gdxpds.gdx.GdxFile`, {py:class}`gdxpds.gdx.GdxSymbol`, {py:class}`gdxpds.gdx.GamsDomainType`
- {py:class}`gdxpds.gdx.GamsDataType`, {py:class}`gdxpds.gdx.GamsVariableType`, {py:class}`gdxpds.gdx.GamsEquationType`
- {py:func}`gdxpds.gdx.append_set`, {py:func}`gdxpds.gdx.append_alias`
- {py:func}`gdxpds.gdx.append_parameter`

:::{note}
Starting with Version 1.1.0, gdxpds does not allow the *number* of dimensions on a `GdxSymbol` to change once it has been firmly established (as evidenced by `GdxSymbol.num_dims > 0` or `GdxSymbol.num_records > 0`). The dimension *names* (`GdxSymbol.dims`) may still be reassigned in place — the DataFrame columns are renamed automatically — and `GdxSymbol.dataframe` may be set using only the dimensional columns, with `GdxSymbol` filling in the remaining columns with default values.
:::

### Set details

#### Set membership and element text

A Set (or Alias) `GdxSymbol` has a single value column whose entries are the GAMS **element text** — a string, with `""` denoting a member that has no text. Membership itself is conveyed by **row presence**: every row in the DataFrame is a member. On write, the value column is flexible — a Set can be built from the dimension columns alone, from a boolean column (any truthy/falsy value means "member, no text"), or from explicit text strings; only a non-empty string is written as element text.

```python
import gdxpds.gdx
import pandas as pd
with gdxpds.gdx.GdxFile() as gdx:
    s = gdxpds.gdx.append_set(gdx, 's', pd.DataFrame({'i': ['a', 'b', 'c']}))
    s.dataframe['Value'] = ['alpha', '', 'gamma']   # element text; '' = no text
    gdx.write('data.gdx')

df = gdxpds.to_dataframe('data.gdx', 's')
print(df['Value'].tolist())   # ['alpha', '', 'gamma']
```

#### Element ordering and the UEL pool

GDX maintains a single file-wide string table — the **UEL pool** — and every symbol's records are stored sorted by that pool's index. The pool is populated in first-encounter order: whichever symbol introduces a UEL first fixes its index for the rest of the file. A later symbol that references the same UEL gets it back in the earlier symbol's position, regardless of where the UEL appeared in the later symbol's input DataFrame.

This is GAMS file-format behavior (visible directly via `gdxdump`), not a `gdxpds` choice — it reproduces on both the `gdxcc` and `gams_transfer` engines.

The user-visible symptom shows up most clearly on Sets, since a Set's records *are* its element list:

```python
import pandas as pd
import gdxpds
gdxpds.to_gdx({
    'leading': pd.DataFrame({'i': ['2010', '2015', '2020'], 'Value': [True] * 3}),
    'years':   pd.DataFrame({'i': ['2008', '2010', '2015', '2020'], 'Value': [True] * 4}),
}, 'data.gdx')
print(gdxpds.to_dataframes('data.gdx')['years']['i'].tolist())
# ['2010', '2015', '2020', '2008']  -- 2008 moves to the end because it was
#                                       registered last
```

The same reorder applies to **any** symbol whose dimensions reference reordered UELs — a `Parameter`, `Variable`, or `Equation` indexed by `years` would have its rows in the same `2010, 2015, 2020, 2008` order, not the input order.

**Workaround.** Write the order-sensitive symbol first, so its order fixes the UEL-pool indices for every later symbol that references the same elements. With `years` placed before `leading` in the dict above, `years` round-trips as `['2008', '2010', '2015', '2020']`. Regression coverage lives in [tests/test_set_ordering.py](https://github.com/NatLabRockies/gdx-pandas/blob/main/tests/test_set_ordering.py).

#### Domain Relationships

Any GDX symbol type — Set, Parameter, Variable, Equation — can declare a strict domain over one or more **parent Sets**. The relationship's semantics differ by child type, but the mechanics in GDX (and in `gdxpds`) are identical:

- **Set on Set** is a *subset* relationship: `set sub_a(a)` declares `sub_a` ⊆ `a` (only members of `a` may appear in `sub_a`).
- **Parameter / Variable / Equation on Set** is an *indexed-over* relationship: `parameter p(a)` declares that `p` is defined on `a`'s elements.

GDX records the relationship per symbol via the {c:func}`gdxSymbolGetDomainX` / {c:func}`gdxSymbolSetDomain` API. The parent named in each domain slot must be a Set or Alias-of-Set — this is what `gdxSymbolSetDomain` enforces at write time. `gdxpds` surfaces it through three complementary attributes on `GdxSymbol`:

- `GdxSymbol.dims` is the always-string list of dimension labels (today's API; unchanged).
- `GdxSymbol.domain` is an optional list of parent-set *references* — each entry is either a `GdxSymbol` object (the parent) or `None` (the wildcard `'*'`). When set, this attribute flags the symbol for strict (`gdxSymbolSetDomain`) writes.
- `GdxSymbol.domain_type` is a derived {py:class}`gdxpds.gdx.GamsDomainType` — `NONE`, `RELAXED`, or `REGULAR` — matching the GDX-level codes from {c:func}`gdxSymbolGetDomainX`.

**Viewing on read.** When a GDX file with strict-domain symbols is read in, the relationships are reconstructed as `GdxSymbol` references:

```python
import gdxpds.gdx
with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
    gdx.read('data.gdx')
    sub = gdx['sub_a']
    print(sub.domain_type)           # GamsDomainType.REGULAR
    print(sub.domain[0] is gdx['a']) # True — points at the parent Set
    print(sub.dims)                  # ['a'] — the string view also works
```

**Setting on write.** Build a parent Set, then a child whose `domain=` references it:

```python
import gdxpds.gdx
import pandas as pd

with gdxpds.gdx.GdxFile() as gdx:
    gdx.append(gdxpds.gdx.GdxSymbol('a', gdxpds.gdx.GamsDataType.Set, dims=['a']))
    gdx[-1].dataframe = pd.DataFrame(
        [['a1', True], ['a2', True], ['a3', True]],
        columns=['a', 'Value'])

    gdx.append(gdxpds.gdx.GdxSymbol(
        'sub_a', gdxpds.gdx.GamsDataType.Set,dims=['a'], domain=[gdx['a']]))
    gdx[-1].dataframe = pd.DataFrame(
        [['a1', True], ['a3', True]], columns=['a', 'Value'])

    gdx.write('data.gdx')
```

The same `domain=[parent]` mechanism works for a Parameter (or Variable, or Equation) child — only the semantics differ ("indexed over `a`" rather than "subset of `a`"):

```python
import gdxpds.gdx
import pandas as pd

with gdxpds.gdx.GdxFile() as gdx:
    gdx.append(gdxpds.gdx.GdxSymbol('a', gdxpds.gdx.GamsDataType.Set, dims=['a']))
    gdx[-1].dataframe = pd.DataFrame(
        [['a1', True], ['a2', True], ['a3', True]],
        columns=['a', 'Value'])

    # Parameter strictly indexed over 'a'. Same `domain=[parent]` reference,
    # same gdxSymbolSetDomain write path.
    gdx.append(gdxpds.gdx.GdxSymbol(
        'p', gdxpds.gdx.GamsDataType.Parameter, dims=['a'], domain=[gdx['a']]))
    gdx[-1].dataframe = pd.DataFrame(
        [['a1', 1.0], ['a2', 2.0], ['a3', 3.0]], columns=['a', 'Value'])

    gdx.write('data.gdx')
```

The convenience functions {py:func}`gdxpds.gdx.append_set` and {py:func}`gdxpds.gdx.append_parameter` both accept a `domain=` kwarg and return the appended `GdxSymbol`, so the same flow chains naturally for either child type:

```python
parent = gdxpds.gdx.append_set(gdx, 'a', pd.DataFrame({'a': ['a1', 'a2']}))
sub    = gdxpds.gdx.append_set(gdx, 'sub_a', pd.DataFrame({'a': ['a1']}), domain=[parent])
p      = gdxpds.gdx.append_parameter(
    gdx, 'p', pd.DataFrame({'a': ['a1', 'a2'], 'Value': [1.0, 2.0]}), domain=[parent])
```

Passing a `GdxSymbol` reference in `domain` is the only trigger for strict writes. Plain strings via `dims=` always stay relaxed — there is no auto-promotion from name to ref.

:::{note}
The two I/O engines differ on one strict-domain edge case: a domain that *mixes* a strict parent with a wildcard — e.g. `domain=[gdx['a'], None]`, which is `('a', '*')` — is written as a **regular** (strict) domain by the `gdxcc` engine but **relaxed** by `gams.transfer`. If you need such a partial-wildcard domain recorded as strict, write that file with `engine="gdxcc"`. Fully-strict and fully-relaxed domains round-trip identically on both engines.
:::

**Write order matters.** Strict {c:func}`gdxSymbolSetDomain` validates that the named parent already exists in the GDX symbol table at the moment the child's write begins. Symbols are written in `GdxFile._symbols` insertion order, so parents must be appended (or otherwise placed) before their children. If the order is wrong at write time, the strict path is skipped for that symbol and `gdxpds` falls back to relaxed (logging an info message); the resulting GDX is still valid. Two ways to fix:

```python
# After building the file in any order:
gdx.reorder_for_strict_domains()   # stable topological sort
gdx.write('data.gdx')
```

or simply build in dependency order from the start.

**Mutation rules.** Setting `domain` after a symbol has records updates the strict refs and renames the DataFrame column headers to match the parent names. Setting `dims` clears any strict refs (the two attributes are coupled and last write wins). Removing a parent (`del gdx['a']`) or replacing it with a same-name symbol is fine — strict resolution happens by name at write time.

If you prefer the simpler API that works with dicts of DataFrames, see [the `domains=` kwarg on `to_gdx` and `gdxpds.get_subset_relationships()`](#direct-conversion) for the string-based equivalent.

#### Aliases

GAMS lets one Set be an *alias* of another — `alias(t, at)` makes `at` another name for set `t`. `gdxpds` surfaces the relationship on `GdxSymbol`:

- `GdxSymbol.data_type` is {py:attr}`gdxpds.gdx.GamsDataType.Alias`, and the symbol reads like the Set it aliases (same elements, same element text).
- `GdxSymbol.alias_of` is the parent Set as a `GdxSymbol` reference (or `None` for non-aliases). Unlike a relaxed domain, an alias has no fallback: its parent must exist in the file when it is written, or the write raises {py:class}`gdxpds.DomainError`.
- `GdxSymbol.dataframe` on an alias is a live **view** of the parent's `dataframe` -- no copy, and no per-alias slot. Mutating the parent shows through the alias immediately; direct assignment to an alias's `dataframe` raises (the alias has no records of its own to set).

**Viewing on read:**

```python
import gdxpds.gdx
with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
    gdx.read('data.gdx')
    at = gdx['at']
    print(at.data_type)                 # GamsDataType.Alias
    print(at.alias_of is gdx['t'])  # True — points at the parent Set
```

**Setting on write.** Build the parent Set, then the alias — via {py:func}`gdxpds.gdx.append_alias` or a `GdxSymbol` with `alias_of`:

```python
import gdxpds.gdx
import pandas as pd
with gdxpds.gdx.GdxFile() as gdx:
    t = gdxpds.gdx.append_set(gdx, 't', pd.DataFrame({'i': ['a', 'b', 'c']}))
    gdxpds.gdx.append_alias(gdx, 'at', t)   # or append_alias(gdx, 'at', 't')
    gdx.write('data.gdx')
```

Aliases must be written after their parent Set. As with strict domains, build in dependency order or call {py:meth}`gdxpds.gdx.GdxFile.reorder_for_strict_domains` before writing.

From the dict-of-DataFrames API, pass an `aliases=` mapping (alias name → parent Set name) to {py:func}`gdxpds.to_gdx`, and read the relationships back with {py:func}`gdxpds.get_aliases`:

```python
gdxpds.to_gdx(dataframes, 'data.gdx', aliases={'at': 't'})
print(gdxpds.get_aliases('data.gdx'))   # {'at': 't'}
```

:::{note}
Aliases of a *named Set* (the common case) are fully supported on both engines. A **universe alias** — an alias of the universe set `*` (`alias_of` resolves to the file's `universal_set`) — reads without error and round-trips within a single engine, but the engines disagree on its membership (`gdxcc` includes the `*` element, `gams.transfer` does not), so it is not cross-engine identical.

**Chained aliases (alias of an alias)** are also supported: GDX itself permits a chain (`aat -> at -> t`), and both engines accept it on write and resolve `alias_of` to a same-file symbol on read. The two engines differ on what reaches disk: the `gdxcc` engine preserves the chain (`aat -> at`), while `gams_transfer` flattens to the root (`aat -> t`). Either form reads back identically through `gdxpds`.
:::

### Parameter, Variable, and Equation details

Parameters store a single float-valued column; Variables and Equations carry five (level, marginal, lower, upper, scale; see {py:data}`gdxpds.gdx.GAMS_VALUE_COLS_MAP`). Their value columns share the same special-value handling — see [Special values](#special-values) below.

#### Variable and Equation types

GAMS distinguishes Variables and Equations by *type* — `positive`, `binary`, `free`, etc. for Variables; `==`, `>=`, `<=`, etc. for Equations. `gdxpds` surfaces these as enum-valued attributes on `GdxSymbol`:

- {py:class}`gdxpds.gdx.GamsVariableType` — `Unknown`, `Binary`, `Integer`, `Positive`, `Negative`, `Free`, `SOS1`, `SOS2`, `Semicont`, `Semiint`. Available on `GdxSymbol.variable_type` when `data_type == GamsDataType.Variable`.
- {py:class}`gdxpds.gdx.GamsEquationType` — `Equality`, `GreaterThan`, `LessThan`, `NothingEnforced`, `External`, `Conic`. Available on `GdxSymbol.equation_type` when `data_type == GamsDataType.Equation`.

The type round-trips on read/write (it is stored in `gdxSymbolInfoX`'s `userinfo` field). For other symbol kinds these attributes are `None`.

#### Special values

In the value columns of Parameters, Variables, and Equations, GAMS's special values map to their Python/numpy equivalents on read, and back on write:

| GAMS | gdxpds (DataFrame) |
|---|---|
| `NA` | `numpy.nan` |
| `UNDEF` | `None` |
| `+Inf` / `-Inf` | `numpy.inf` / `-numpy.inf` |
| `EPS` | `numpy.finfo(float).tiny` (smallest normal positive float, ~2.22e-308) |

Both I/O engines preserve all of these on write, so they round-trip unchanged.

EPS detection is exact equality on the sentinel: only `numpy.finfo(float).tiny` maps to GAMS `EPS` on write. Other small floats (`1e-200`, machine epsilon, etc.) survive as-is. In v3.x and earlier, the sentinel was `numpy.finfo(float).eps` (~2.22e-16) and any value below it silently became `EPS` (#39).

:::{tip}
If you would rather **not** keep `EPS` values, drop them yourself before writing — there is no write-time option, so the transformation stays explicit and engine-independent:

```python
import numpy as np
eps = np.finfo(float).tiny
df['Value'] = df['Value'].replace(eps, 0.0)   # treat EPS as plain zero
```
:::

## Migration from 1.x / 2.x / 3.x

Releases 2.0.0, 3.0.0, and 4.0.0 each made breaking changes. In v2.0.0, old interfaces were removed for `to_dataframe` and accessing the `csv_to_gdx` and `gdx_to_csv` scripts. Version 3.0.0 switches the default engine to `gams.transfer` (from `gdxcc`) and supports set text and aliases as first-class features. Version 4.0.0 fixes the GAMS `EPS` encoding so legitimate small floats no longer round-trip as `EPS`. Additional details and other breaking changes:

### v2.0.0 breaking changes

- **`to_dataframe()` always returns a plain DataFrame.** The `old_interface` argument is gone; drop any `old_interface=False` (the old `old_interface=True` returned a `{symbol_name: DataFrame}` dict).
- **The `.py`-suffixed CLI commands were removed.** Use `csv_to_gdx` / `gdx_to_csv` (not `csv_to_gdx.py` / `gdx_to_csv.py`).
- **The optional `gdx2py` read accelerator was removed** (not an API change); Parameter reads use the standard path.

### v3.0.0 breaking changes

- **Default I/O engine is now `gams.transfer`** (when a compatible `gamsapi` is installed), falling back to `gdxcc`. To keep the previous behavior, pass `engine="gdxcc"` or set `GDXPDS_ENGINE=gdxcc`.
- **Set/Alias values are element-text strings, not booleans.** Reading a Set now yields a string value column (`""` for no text); membership is row presence. Code that checked a Set's value as a boolean should switch to testing row presence (or, for text, the string).
- **`load_set_text` is removed.** Element text is always read and written, so drop the argument from any `to_dataframe` / `to_dataframes` / `GdxSymbol.load` / `GdxFile.load_all` / `load_symbols` calls.
- **`GdxFile.H` is removed.** If you drove raw `gdxcc` calls through it, use `gdx_file._engine_impl.handle` instead.
- **GDX UNDEF is preserved on write** (round-trips as `None`) instead of collapsing to `0.0`.

### v4.0.0 breaking changes

- **GAMS `EPS` is now encoded as `numpy.finfo(float).tiny`** (~2.22e-308), the smallest normal positive float, not `numpy.finfo(float).eps` (machine epsilon, ~2.22e-16). EPS detection is exact equality on this sentinel, so a legitimate small float (`1e-200`, machine epsilon, etc.) now round-trips as itself instead of silently mapping to GAMS `EPS` (#39). If your code wrote `numpy.finfo(float).eps` to mean GAMS `EPS`, switch to `numpy.finfo(float).tiny`; `gdxpds.special.NUMPY_SPECIAL_VALUES[-1]` is always the canonical sentinel.

## Configuration

Two runtime choices control how gdxpds talks to GAMS, and both are set the same three ways — a keyword argument, an environment variable, or (for the command-line utilities) a flag. In each case the explicit keyword wins, then the environment variable, then a fallback:

| Setting | Keyword | Environment variable | CLI flag | Fallback |
|---|---|---|---|---|
| GAMS install location | `gams_dir=` | `GAMS_DIR`, then `GAMSDIR` | `-g` / `--gams_dir` | auto-discovery: `where`/`which gams`, then the newest install under `C:\GAMS` |
| I/O engine | `engine=` | `GDXPDS_ENGINE` | `-b` / `--engine` | `gams_transfer` when usable, otherwise `gdxcc` |

The keyword arguments are accepted by every read/write entry point — {py:func}`gdxpds.to_dataframes`, {py:func}`gdxpds.to_dataframe`, {py:func}`gdxpds.list_symbols`, {py:func}`gdxpds.get_data_types`, {py:func}`gdxpds.get_subset_relationships`, {py:func}`gdxpds.to_gdx`, and {py:class}`gdxpds.gdx.GdxFile`. Either choice may be omitted to use its fallback.

Direct conversion example:

```python
import gdxpds

dataframes = gdxpds.to_dataframes('data.gdx', gams_dir=r'C:\GAMS\48', engine='gams_transfer')
gdxpds.to_gdx(dataframes, 'out.gdx', gams_dir=r'C:\GAMS\48', engine=gdxpds.Engine.GAMS_TRANSFER)
```

Object-Oriented API example:

```python
import gdxpds.gdx

with gdxpds.gdx.GdxFile(gams_dir=r'C:\GAMS\48', engine='gams_transfer') as f:
    f.read('data.gdx')
```

The same two choices are flags on the `gdx_to_csv` / `csv_to_gdx` utilities:

::::{tab-set}
:::{tab-item} POSIX (Mac/Linux)
```bash
gdx_to_csv -i data.gdx -o out_dir --gams_dir /opt/gams/48 --engine gams_transfer
csv_to_gdx -i data.txt -o out.gdx --gams_dir /opt/gams/48 --engine gams_transfer
```
:::
:::{tab-item} Windows (CMD)
```bat
gdx_to_csv -i data.gdx -o out_dir --gams_dir C:\GAMS\48 --engine gams_transfer
csv_to_gdx -i data.txt -o out.gdx --gams_dir C:\GAMS\48 --engine gams_transfer
```
:::
:::{tab-item} Windows (PowerShell)
```powershell
gdx_to_csv -i data.gdx -o out_dir --gams_dir 'C:\GAMS\48' --engine gams_transfer
csv_to_gdx -i data.txt -o out.gdx --gams_dir 'C:\GAMS\48' --engine gams_transfer
```
:::
::::

Or set either one once — for a whole process or shell session — through its environment variable instead of passing it at every call site:

::::{tab-set}
:::{tab-item} POSIX (Mac/Linux)
```bash
export GAMS_DIR=/opt/gams/48
export GDXPDS_ENGINE=gams_transfer
gdx_to_csv -i data.gdx -o out_dir
```
:::
:::{tab-item} Windows (CMD)
```bat
set GAMS_DIR=C:\GAMS\48
set GDXPDS_ENGINE=gams_transfer
gdx_to_csv -i data.gdx -o out_dir
```
:::
:::{tab-item} Windows (PowerShell)
```powershell
$Env:GAMS_DIR = 'C:\GAMS\48'
$Env:GDXPDS_ENGINE = 'gams_transfer'
gdx_to_csv -i data.gdx -o out_dir
```
:::
::::

### GAMS install location (`gams_dir`)

gdxpds always needs to locate your GAMS installation, because the GDX shared library lives there (not in the Python package) — so `gams_dir` is required even when the Python bindings are pip-installed. See [Install → Preliminaries](index.md#preliminaries) for the recommended setup. If you do not set it explicitly, gdxpds auto-discovers it as described in the table above; calling `gdxpds info` on the command line reports which directory was chosen and via which route.

### I/O engine (`engine`)

gdxpds can move data between GDX files and DataFrames through either of two engines, named by string or {py:class}`gdxpds.Engine` value. ("Engine" here means the underlying read/write implementation — the SWIG-bound `gdxcc` or the higher-level `gams.transfer`; distinct from the `GdxFile` / `GdxSymbol` object model in [Object-Oriented API](#object-oriented-api) above.)

- **`"gams_transfer"`** (the default, when usable) uses GAMS's `gams.transfer` library (shipped inside `gamsapi`). It is **much faster on large files** — roughly 2× faster to read and 4× faster to write a ~2 MB GDX, widening to an order of magnitude or more on hundreds-of-MB files — but its fixed per-file overhead makes it *slower* than `gdxcc` on very small files.
- **`"gdxcc"`** (the fallback) uses SWIG-bound `gdxcc` calls and works with either GAMS Python binding.

`gams.transfer` is only usable when a compatible `gamsapi` is installed (see [Install → Preliminaries](index.md#preliminaries)); this is checked at runtime and stored in `gdxpds.HAVE_GAMS_TRANSFER`. The **default** prefers `gams.transfer` and quietly falls back to `gdxcc` when it isn't usable, so gdxcc-only environments are unaffected. An *explicit* request for an unavailable engine raises {py:class}`gdxpds.EngineError` rather than falling back. Both engines produce identical DataFrames and GDX files under almost all circumstances — see the [Domain Relationships](#domain-relationships) and [Aliases](#aliases) notes above for the known edge cases.

Two behavioral differences between the engines:

- **Pre-load symbol metadata.** Before a symbol's records are loaded, `gdxcc` exposes a symbol's record count (`GdxSymbol.num_records`) from the GDX header, whereas `gams.transfer` does not (it reads as `0` until the records are loaded). After loading — i.e. once you touch `GdxSymbol.dataframe`, or read with `lazy_load=False` — `num_records` reflects the DataFrame on both engines.
- **File-level metadata.** `GdxFile.version` and `GdxFile.producer` come from the GDX header and are read populated only by the `gdxcc` engine; the `gams_transfer` engine leaves them as `None`.
