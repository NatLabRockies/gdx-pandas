# Overview

There are two main ways to use gdxpds. The first use case is the one that was initially supported: direct conversion between GDX files on disk and pandas DataFrames or a csv version thereof. Starting with the Version 1.0.0 rewrite, there is now a second style of use which involves interfacing with GDX files and symbols via the {py:class}`gdxpds.gdx.GdxFile` and {py:class}`gdxpds.gdx.GdxSymbol` classes.

[Direct Conversion](#direct-conversion) | [Backend Classes](#backend-classes)

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
- {py:func}`gdxpds.to_dataframe` (If the call to this method includes `old_interface=False`, then the return value will be a plain DataFrame, not a `{'symbol_name': df}` dict.)

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
- {py:func}`gdxpds.gdx.append_set`
- {py:func}`gdxpds.gdx.append_parameter`

Starting with Version 1.1.0, gdxpds does not allow `GdxSymbol.dims` to change once they have been firmly established (as evidenced by `GdxSymbol.num_dims > 0` or `GdxSymbol.num_records > 0`), but does allow `GdxSymbol.dataframe` to be set using the dimensional columns alone. In that use case, `GdxSymbol` fills in the remaining dataframe columns with default values.
