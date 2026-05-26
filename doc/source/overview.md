# Overview

There are two main ways to use gdxpds. The first use case is the one that was initially supported: direct conversion between GDX files on disk and pandas DataFrames or a csv version thereof. Starting with the Version 1.0.0 rewrite, there is now a second style of use which involves interfacing with GDX files and symbols via the {py:class}`gdxpds.gdx.GdxFile` and {py:class}`gdxpds.gdx.GdxSymbol` classes. Either way, [Configuration](#configuration) — where to find GAMS and which I/O engine to use — works the same.

[Direct Conversion](#direct-conversion) | [Backend Classes](#backend-classes) | [Configuration](#configuration)

## Direct Conversion

The two primary points of reference for the direct conversion utilities are GDX files on disk and python dicts of `{symbol_name: pandas.DataFrame}`, where each `pandas.DataFrame` contains data for a single set, parameter, equation, or variable. For sets and parameters, the last column of the DataFrame is assumed to contain the value of the element, which for sets should be `True`, and for parameters should be a `float` (or one of the {py:const}`gdxpds.special.NUMPY_SPECIAL_VALUES`). Equations and variables have additional 'value' columns, in particular a level, a marginal value, a lower bound, an upper bound, and a scale, as enumerated in {py:class}`gdxpds.gdx.GamsValueType`. These values are all assumed to be found in the last five columns of the DataFrame, also see {py:data}`gdxpds.gdx.GAMS_VALUE_COLS_MAP`.

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
- {py:func}`gdxpds.get_subset_relationships` — read the subset (domain) relationships out of a GDX file, returned as `{symbol_name: [parent_name_or_None_for_wildcard, ...]}`.

To create a GDX with strict subset relationships from the direct-conversion API, pass a `domains=` mapping to {py:func}`gdxpds.to_gdx`:

```python
import gdxpds
import pandas as pd

dataframes = {
    'a':     pd.DataFrame([['a1', True], ['a2', True], ['a3', True]], columns=['a', 'Value']),
    'sub_a': pd.DataFrame([['a1', True], ['a3', True]],                columns=['a', 'Value']),
}
gdxpds.to_gdx(dataframes, 'data.gdx', domains={'sub_a': ['a']})

# Read the relationship info back. Domain names are reported verbatim; only
# the wildcard '*' (or a dimension with no recorded domain) comes through as
# None. Here 'a' is a root Set whose single dimension is labeled with its own
# name, so it round-trips as 'a' rather than None.
print(gdxpds.get_subset_relationships('data.gdx'))
# {'a': ['a'], 'sub_a': ['a']}
```

The `domains=` keys are child symbol names; each value is the list of parent Set names (or `None` for the wildcard `'*'`), one entry per dimension. `to_gdx` topologically sorts the input so each parent is written before its children. The Direct Conversion API is **string-based** — parents are named by string. For an object-reference-based API (live links to parent `GdxSymbol`s, useful when mutating or composing files in Python), see [Subset (Domain) Relationships](#subset-domain-relationships) under Backend Classes.

The package also includes command line utilities for converting between GDX and CSV. After `pip install gdxpds`, these are on `PATH`:

```bash
gdx_to_csv --help
csv_to_gdx --help
```

## Backend Classes

The basic functionalities described above can also be achieved with direct use of the backend classes available in {py:mod}`gdxpds.gdx`. To duplicate the GDX read functionality shown above one would write:

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

And enables more transparent creation of new GDX files:

```python
from itertools import product

from gdxpds.gdx import GdxFile, GdxSymbol, GamsDataType, append_set, append_parameter
import pandas as pd

out_file = 'my_new_gdx_data.gdx'
with GdxFile() as gdx:

    # Create a new set with one dimension
    gdx.append(GdxSymbol('my_set', GamsDataType.Set, dims=['u']))
    data = pd.DataFrame([['u' + str(i)] for i in range(1, 11)])
    data['Value'] = True
    gdx[-1].dataframe = data

    # Create a new parameter with one dimension
    gdx.append(GdxSymbol('my_parameter', GamsDataType.Parameter, dims=['u']))
    data = pd.DataFrame([['u' + str(i), i * 100] for i in range(1, 11)],
                        columns=(gdx[-1].dims + gdx[-1].value_col_names))
    gdx[-1].dataframe = data

    # Create new sets with convenience function append_set
    append_set(gdx, "my_other_set", pd.DataFrame(
        [['v' + str(i)] for i in range(1, 6)], columns=['v'])
    )
    append_set(gdx, "my_combo_set", pd.DataFrame(
        product(['u' + str(i) for i in range(1, 11)], ['v' + str(i) for i in range(1, 6)]),
        columns=['u', 'v'])
    )

    # Create a new parameter with convenience function append_parameter
    df = gdx[-1].dataframe.copy()
    df.loc[:, 'Value'] = 1.0
    append_parameter(gdx, 'my_other_parameter', df)

    # Write the file to disk
    gdx.write(out_file)
```

The key classes and functions for directly using the backend are:

- {py:class}`gdxpds.gdx.GdxFile`
- {py:class}`gdxpds.gdx.GdxSymbol`
- {py:class}`gdxpds.gdx.GamsDataType`
- {py:class}`gdxpds.gdx.GamsDomainType`
- {py:func}`gdxpds.gdx.append_set`
- {py:func}`gdxpds.gdx.append_parameter`
- {py:func}`gdxpds.gdx.append_alias`

Starting with Version 1.1.0, gdxpds does not allow the *number* of dimensions on a `GdxSymbol` to change once it has been firmly established (as evidenced by `GdxSymbol.num_dims > 0` or `GdxSymbol.num_records > 0`). The dimension *names* (`GdxSymbol.dims`) may still be reassigned in place — the DataFrame columns are renamed automatically — and `GdxSymbol.dataframe` may be set using only the dimensional columns, with `GdxSymbol` filling in the remaining columns with default values.

### Subset (Domain) Relationships

A Set in GAMS may be declared as a *subset* of another Set — `set sub_a(a)` declares `sub_a` over the domain `a`. GDX records this relationship per symbol via the {c:func}`gdxSymbolGetDomainX` / {c:func}`gdxSymbolSetDomain` API. `gdxpds` surfaces it through two complementary attributes on `GdxSymbol`:

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

The convenience functions {py:func}`gdxpds.gdx.append_set` and {py:func}`gdxpds.gdx.append_parameter` both accept a `domain=` kwarg and return the appended `GdxSymbol`, so the same flow chains naturally:

```python
parent = gdxpds.gdx.append_set(gdx, 'a', pd.DataFrame({'a': ['a1', 'a2']}))
child  = gdxpds.gdx.append_set(gdx, 'sub_a', pd.DataFrame({'a': ['a1']}), domain=[parent])
```

Passing a `GdxSymbol` reference in `domain` is the only trigger for strict writes. Plain strings via `dims=` always stay relaxed — there is no auto-promotion from name to ref.

:::{note}
The two I/O engines differ on one strict-domain edge case: a domain that *mixes* a strict parent with a wildcard — e.g. `domain=[gdx['a'], None]`, which is `('a', '*')` — is written as a **regular** (strict) domain by the `gdxcc` engine but **relaxed** by `gams.transfer`. If you need such a partial-wildcard domain recorded as strict, write that file with `backend="gdxcc"`. Fully-strict and fully-relaxed domains round-trip identically on both engines.
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

### Set membership and element text

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

### Special values

In the value columns of Parameters, Variables, and Equations, GAMS's special values map to their Python/numpy equivalents on read, and back on write:

| GAMS | gdxpds (DataFrame) |
|---|---|
| `NA` | `numpy.nan` |
| `UNDEF` | `None` |
| `+Inf` / `-Inf` | `numpy.inf` / `-numpy.inf` |
| `EPS` | machine epsilon (`numpy.finfo(float).eps`) |

Both I/O engines preserve all of these on write, so they round-trip unchanged.

:::{tip}
If you would rather **not** keep `EPS` values, drop them yourself before writing — there is no write-time option, so the transformation stays explicit and engine-independent:

```python
import numpy as np
eps = np.finfo(float).eps
df['Value'] = df['Value'].replace(eps, 0.0)   # treat EPS as plain zero
```
:::

### Aliases

GAMS lets one Set be an *alias* of another — `alias(t, at)` makes `at` another name for set `t`. `gdxpds` surfaces the relationship on `GdxSymbol`:

- `GdxSymbol.data_type` is {py:attr}`gdxpds.gdx.GamsDataType.Alias`, and the symbol reads like the Set it aliases (same elements, same element text).
- `GdxSymbol.aliased_with` is the parent Set as a `GdxSymbol` reference (or `None` for non-aliases). Unlike a relaxed domain, an alias has no fallback: its parent must exist in the file when it is written, or the write raises {py:class}`gdxpds.DomainError`.

**Viewing on read:**

```python
import gdxpds.gdx
with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
    gdx.read('data.gdx')
    at = gdx['at']
    print(at.data_type)                 # GamsDataType.Alias
    print(at.aliased_with is gdx['t'])  # True — points at the parent Set
```

**Setting on write.** Build the parent Set, then the alias — via {py:func}`gdxpds.gdx.append_alias` or a `GdxSymbol` with `aliased_with`:

```python
import gdxpds.gdx
import pandas as pd
with gdxpds.gdx.GdxFile() as gdx:
    t = gdxpds.gdx.append_set(gdx, 't', pd.DataFrame({'i': ['a', 'b', 'c']}))
    gdxpds.gdx.append_alias(gdx, 'at', t)   # or append_alias(gdx, 'at', 't')
    gdx.write('data.gdx')
```

Aliases must be written after their parent Set. As with strict domains, build in dependency order or call {py:meth}`gdxpds.gdx.GdxFile.reorder_for_strict_domains` before writing.

From the dict-of-DataFrames API, pass an `aliases=` mapping (alias name → parent Set name) to {py:func}`gdxpds.to_gdx`:

```python
gdxpds.to_gdx(dataframes, 'data.gdx', aliases={'at': 't'})
```

:::{note}
Aliases of a *named Set* (the common case) are fully supported on both engines. A **universe alias** — an alias of the universe set `*` (`aliased_with` resolves to the file's `universal_set`) — reads without error and round-trips within a single engine, but the engines disagree on its membership (`gdxcc` includes the `*` element, `gams.transfer` does not), so it is not cross-engine identical.

**Chained aliases (alias of an alias)** are also supported: GDX itself permits a chain (`aat -> at -> t`), and both backends accept it on write and resolve `aliased_with` to a same-file symbol on read. The two engines differ on what reaches disk: the `gdxcc` backend preserves the chain (`aat -> at`), while `gams_transfer` flattens to the root (`aat -> t`). Either form reads back identically through `gdxpds`.
:::

## Migration from 1.x / 2.x

Releases 2.0.0 and 3.0.0 each made breaking changes. If you are upgrading from 1.5.x, you cross both — the relevant changes for callers are collected here.

**3.0.0 (breaking):**

- **Default I/O engine is now `gams.transfer`** (when a compatible `gamsapi` is installed), falling back to `gdxcc`. To keep the previous behavior, pass `backend="gdxcc"` or set `GDXPDS_BACKEND=gdxcc`.
- **Set/Alias values are element-text strings, not booleans.** Reading a Set now yields a string value column (`""` for no text); membership is row presence. Code that checked a Set's value as a boolean should switch to testing row presence (or, for text, the string).
- **`load_set_text` is removed.** Element text is always read and written, so drop the argument from any `to_dataframe` / `to_dataframes` / `GdxSymbol.load` / `GdxFile.load_all` / `load_symbols` calls.
- **`GdxFile.H` is removed.** If you drove raw `gdxcc` calls through it, use `gdx_file._backend_impl.handle` instead.
- **GDX UNDEF is preserved on write** (round-trips as `None`) instead of collapsing to `0.0`.

**2.0.0 (breaking) — also relevant if you skipped it:**

- **`to_dataframe()` always returns a plain DataFrame.** The `old_interface` argument is gone; drop any `old_interface=False` (the old `old_interface=True` returned a `{symbol_name: DataFrame}` dict).
- **The `.py`-suffixed CLI commands were removed.** Use `csv_to_gdx` / `gdx_to_csv` (not `csv_to_gdx.py` / `gdx_to_csv.py`).
- **The optional `gdx2py` read accelerator was removed** (not an API change); Parameter reads use the standard path.

## Configuration

Two runtime choices control how gdxpds talks to GAMS, and both are set the same three ways — a keyword argument, an environment variable, or (for the command-line utilities) a flag. In each case the explicit keyword wins, then the environment variable, then a fallback:

| Setting | Keyword | Environment variable | CLI flag | Fallback |
|---|---|---|---|---|
| GAMS install location | `gams_dir=` | `GAMS_DIR`, then `GAMSDIR` | `-g` / `--gams_dir` | auto-discovery: `where`/`which gams`, then the newest install under `C:\GAMS` |
| I/O engine | `backend=` | `GDXPDS_BACKEND` | `-b` / `--backend` | `gams_transfer` when usable, otherwise `gdxcc` |

The keyword arguments are accepted by every read/write entry point — {py:func}`gdxpds.to_dataframes`, {py:func}`gdxpds.to_dataframe`, {py:func}`gdxpds.list_symbols`, {py:func}`gdxpds.get_data_types`, {py:func}`gdxpds.get_subset_relationships`, {py:func}`gdxpds.to_gdx`, and {py:class}`gdxpds.gdx.GdxFile`. Either choice may be omitted to use its fallback.

Direct conversion example:

```python
import gdxpds

dataframes = gdxpds.to_dataframes('data.gdx', gams_dir=r'C:\GAMS\48', backend='gams_transfer')
gdxpds.to_gdx(dataframes, 'out.gdx', gams_dir=r'C:\GAMS\48', backend=gdxpds.Backend.GAMS_TRANSFER)
```

Backend classes example:

```python
import gdxpds.gdx

with gdxpds.gdx.GdxFile(gams_dir=r'C:\GAMS\48', backend='gams_transfer') as f:
    f.read('data.gdx')
```

The same two choices are flags on the `gdx_to_csv` / `csv_to_gdx` utilities:

```bash
gdx_to_csv -i data.gdx -o out_dir --gams_dir /opt/gams/48 --backend gams_transfer
csv_to_gdx -i data.txt -o out.gdx --gams_dir /opt/gams/48 --backend gams_transfer
```

Or set either one once — for a whole process or shell session — through its environment variable instead of passing it at every call site (PowerShell shown; use `export` on POSIX):

```powershell
$Env:GAMS_DIR = 'C:\GAMS\48'
$Env:GDXPDS_BACKEND = 'gams_transfer'
gdx_to_csv -i data.gdx -o out_dir
```

### GAMS install location (`gams_dir`)

gdxpds always needs to locate your GAMS installation, because the GDX shared library lives there (not in the Python package) — so `gams_dir` is required even when the Python bindings are pip-installed. If you do not set it explicitly, gdxpds auto-discovers it as described in the table above; calling `gdxpds info` on the command line reports which directory was chosen and via which route.

### I/O engine (`backend`)

gdxpds can move data between GDX files and DataFrames through either of two engines, named by string or {py:class}`gdxpds.Backend` value. (This "backend" is a different concept from the [Backend Classes](#backend-classes) above — the `GdxFile` / `GdxSymbol` objects; here it means the underlying read/write *engine*.)

- **`"gams_transfer"`** (the default, when usable) uses GAMS's `gams.transfer` library (shipped inside `gamsapi`). It is **much faster on large files** — roughly 2× faster to read and 4× faster to write a ~2 MB GDX, widening to an order of magnitude or more on hundreds-of-MB files — but its fixed per-file overhead makes it *slower* than `gdxcc` on very small files.
- **`"gdxcc"`** (the fallback) uses SWIG-bound `gdxcc` calls and works with either GAMS Python binding.

`gams.transfer` is only usable when a compatible `gamsapi` is installed (see [Install](index.md#install)); check `gdxpds.HAVE_GAMS_TRANSFER` at runtime. The **default** prefers `gams.transfer` and quietly falls back to `gdxcc` when it isn't usable, so gdxcc-only environments are unaffected. An *explicit* request for an unavailable engine raises {py:class}`gdxpds.BackendError` rather than falling back. Both engines produce identical DataFrames and GDX files.

One behavioral difference between the engines is visible only **before a symbol's records are loaded**: `gdxcc` exposes a symbol's record count (`GdxSymbol.num_records`) and the file's `version`/`producer` from the GDX header, whereas `gams.transfer` does not (they read as `0` / `None` until the records are loaded). After loading — i.e. once you touch `GdxSymbol.dataframe`, or read with `lazy_load=False` — `num_records` reflects the DataFrame on both engines.
