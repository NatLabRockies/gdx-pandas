import logging

import numpy as np

logger = logging.getLogger(__name__)


#                      1E300,  2E300,  3E300,   4E300,               5E300
NUMPY_SPECIAL_VALUES = [None, np.nan, np.inf, -np.inf, np.finfo(float).eps]
"""List of numpy special values in gdxGetSpecialValues order, i.e.,
[None, np.nan, np.inf, -np.inf, np.finfo(float).eps]
"""


def convert_gdx_to_np_svs(df, num_dims):
    """
    Converts GDX special values to the corresponding numpy versions.

    Parmeters
    ---------
    df : pandas.DataFrame
        a GdxSymbol DataFrame as it was read directly from GDX
    num_dims : int
        the number of columns in df that list the dimension values for which the
        symbol value is non-zero / non-default

    Returns
    -------
    pandas.DataFrame
        copy of df for which all GDX special values have been converted to
        their numpy equivalents
    """

    # create clean copy of df
    tmp = df.copy()

    # apply the map to the value columns and merge with the dimensional information
    tmp = (tmp.iloc[:, :num_dims]).merge(
        tmp.iloc[:, num_dims:].replace(GDX_TO_NP_SVS), left_index=True, right_index=True
    )
    return tmp


def is_np_eps(val):
    """
    Parameters
    ----------
    val : numeric
        value to test

    Returns
    -------
    bool
        True if val is equal to eps (np.finfo(float).eps), False otherwise
    """
    return np.abs(val - NUMPY_SPECIAL_VALUES[-1]) < NUMPY_SPECIAL_VALUES[-1]


def is_np_sv(val):
    """
    Parameters
    ----------
    val : numeric
        value to test

    Returns
    -------
    bool
        True if val is NaN, eps, or is in NUMPY_SPECIAL_VALUES; False otherwise
    """
    return np.isnan(val) or (val in NUMPY_SPECIAL_VALUES) or is_np_eps(val)


def convert_np_to_gdx_svs(df, num_dims):
    """
    Converts numpy special values to the corresponding GDX versions.

    Parmeters
    ---------
    df : pandas.DataFrame
        a GdxSymbol DataFrame in pandas/numpy form
    num_dims : int
        the number of columns in df that list the dimension values for which the
        symbol value is non-zero / non-default

    Returns
    -------
    pandas.DataFrame
        copy of df for which all numpy special values have been converted to
        their GDX equivalents
    """

    # get a clean copy of df
    tmp = df.copy()

    # fillna and apply map to value columns, then merge with dimensional columns
    try:
        values = tmp.iloc[:, num_dims:].replace(NP_TO_GDX_SVS, value=None)
        # DataFrame.replace is generally not sufficient to identify EPS values
        values[(values - NUMPY_SPECIAL_VALUES[-1]).abs() < NUMPY_SPECIAL_VALUES[-1]] = (
            SPECIAL_VALUES[4]
        )
        tmp = (tmp.iloc[:, :num_dims]).merge(values, left_index=True, right_index=True)
    except Exception:
        logger.error(
            "Unable to convert numpy special values to GDX special values. "
            f"num_dims: {num_dims}, dataframe:\n{df}"
        )
        raise
    return tmp


def pd_isnan(val):
    """
    Utility function for identifying None or NaN (which are indistinguishable in pandas).

    Parameters
    ----------
    val : numeric
        value to test

    Returns
    -------
    bool
        True if val is a GDX encoded special value that maps to None or numpy.nan;
        False otherwise
    """
    return val is None or val != val


def pd_val_equal(val1, val2):
    """
    Utility function used to test special value conversions.

    Parameters
    ----------
    val1 : float or None
        first value to compare
    val2 : float or None
        second value to compare

    Returns
    -------
    bool
        True if val1 and val2 are equal in the sense of == or they are
        both NaN/None. The values that map to None and np.nan are assumed
        to be equal because pandas cannot be relied upon to make the
        distinction.
    """
    return pd_isnan(val1) and pd_isnan(val2) or val1 == val2


def gdx_isnan(val, gdxf):
    """
    Utility function for equating the GDX special values that map to None or NaN
    (which are indistinguishable in pandas).

    Parameters
    ----------
    val : numeric
        value to test
    gdxf : GdxFile
        GDX file containing the value. Provides np_to_gdx_svs map.

    Returns
    -------
    bool
        True if val is a GDX encoded special value that maps to None or numpy.nan;
        False otherwise
    """
    return val in [SPECIAL_VALUES[0], SPECIAL_VALUES[1]]


def gdx_val_equal(val1, val2, gdxf):
    """
    Utility function used to test special value conversions.

    Parameters
    ----------
    val1 : float or GDX special value
        first value to compare
    val2 : float or GDX special value
        second value to compare
    gdxf : GdxFile
        GDX file containing val1 and val2

    Returns
    -------
    bool
        True if val1 and val2 are equal in the sense of == or they are
        equivalent GDX-format special values. The values that map to None
        and np.nan are assumed to be equal because pandas cannot be relied
        upon to make the distinction.
    """
    if gdx_isnan(val1, gdxf) and gdx_isnan(val2, gdxf):
        return True
    return val1 == val2


def load_specials(gams_dir_finder):
    """
    Load special values

    Needs to be called after gdxcc is loaded. Populates the module attributes
    SPECIAL_VALUES, GDX_TO_NP_SVS, and NP_TO_GDX_SVS.

    Parameters
    ----------
    gams_dir_finder : :class:`gdxpds.tools.GamsDirFinder`
    """
    global SPECIAL_VALUES
    global GDX_TO_NP_SVS
    global NP_TO_GDX_SVS

    # Imported lazily so `import gdxpds` succeeds with no binding installed; this
    # function only runs once a GDX op has located GAMS.
    try:
        from gams.core import gdx as gdxcc
    except ImportError:
        import gdxcc

    from gdxpds.tools import _GdxHandle, _require_gams_installation

    _require_gams_installation(gams_dir_finder)

    with _GdxHandle(gdxcc, gams_dir_finder.gams_dir, gams_dir_finder.source) as h:
        # get special values
        special_values = gdxcc.doubleArray(gdxcc.GMS_SVIDX_MAX)
        gdxcc.gdxGetSpecialValues(h.H, special_values)

        SPECIAL_VALUES = []
        GDX_TO_NP_SVS = {}
        NP_TO_GDX_SVS = {}
        for i in range(gdxcc.GMS_SVIDX_MAX):
            if i >= len(NUMPY_SPECIAL_VALUES):
                break
            SPECIAL_VALUES.append(special_values[i])
            gdx_val = special_values[i]
            GDX_TO_NP_SVS[gdx_val] = NUMPY_SPECIAL_VALUES[i]
            NP_TO_GDX_SVS[NUMPY_SPECIAL_VALUES[i]] = gdx_val


# These values are populated by load_specials, called in load_gdxcc
SPECIAL_VALUES = []
GDX_TO_NP_SVS = {}
NP_TO_GDX_SVS = {}
