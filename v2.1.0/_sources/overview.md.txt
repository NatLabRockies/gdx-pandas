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

**Write order matters.** Strict {c:func}`gdxSymbolSetDomain` validates that the named parent already exists in the GDX symbol table at the moment the child's write begins. Symbols are written in `GdxFile._symbols` insertion order, so parents must be appended (or otherwise placed) before their children. If the order is wrong at write time, the strict path is skipped for that symbol and `gdxpds` falls back to relaxed (logging an info message); the resulting GDX is still valid. Two ways to fix:

```python
# After building the file in any order:
gdx.reorder_for_strict_domains()   # stable topological sort
gdx.write('data.gdx')
```

or simply build in dependency order from the start.

**Mutation rules.** Setting `domain` after a symbol has records updates the strict refs and renames the DataFrame column headers to match the parent names. Setting `dims` clears any strict refs (the two attributes are coupled and last write wins). Removing a parent (`del gdx['a']`) or replacing it with a same-name symbol is fine — strict resolution happens by name at write time.

If you prefer the simpler API that works with dicts of DataFrames, see [the `domains=` kwarg on `to_gdx` and `gdxpds.get_subset_relationships()`](#direct-conversion) for the string-based equivalent.

## Configuration

Two runtime choices control how gdxpds talks to GAMS, and both are set the same three ways — a keyword argument, an environment variable, or (for the command-line utilities) a flag. In each case the explicit keyword wins, then the environment variable, then a fallback:

| Setting | Keyword | Environment variable | CLI flag | Fallback |
|---|---|---|---|---|
| GAMS install location | `gams_dir=` | `GAMS_DIR`, then `GAMSDIR` | `-g` / `--gams_dir` | auto-discovery: `where`/`which gams`, then the newest install under `C:\GAMS` |
| I/O engine | `backend=` | `GDXPDS_BACKEND` | `-b` / `--backend` | `gdxcc` |

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

- **`"gdxcc"`** (the legacy engine and current default) uses SWIG-bound `gdxcc` calls and works with either GAMS Python binding.
- **`"gams_transfer"`** uses GAMS's `gams.transfer` library (shipped inside `gamsapi`). It is **much faster on large files** — roughly 2× faster to read and 4× faster to write a ~2 MB GDX, widening to an order of magnitude or more on hundreds-of-MB files — but its fixed per-file overhead makes it *slower* than `gdxcc` on very small files.

`gams.transfer` is only usable when a compatible `gamsapi` is installed (see [Install](index.md#install)); check `gdxpds.HAVE_GAMS_TRANSFER` at runtime. Requesting it when it is unavailable raises {py:class}`gdxpds.BackendError` rather than silently falling back. Both engines produce identical DataFrames and GDX files.
