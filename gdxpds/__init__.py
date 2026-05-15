__version__ = "1.4.0"

import logging
import sys

logger = logging.getLogger(__name__)

from gdxpds.tools import Error
from gdxpds.special import load_specials


def load_gdxcc(gams_dir=None):
    """
    Method to initialize GAMS, especially to load required libraries that can 
    sometimes conflict with other packages.

    Parameters
    ----------
    gams_dir : None or str
        if not None, directory containing the GAMS executable
    """
    if 'pandas' in sys.modules:
        logger.warning("Especially on Linux, gdxpds should be imported before " + \
                       "pandas to avoid a library conflict. Also make sure your " + \
                       "GAMS directory is listed in LD_LIBRARY_PATH.")
    import gdxcc
    from gdxpds.tools import GamsDirFinder
    finder = GamsDirFinder(gams_dir=gams_dir)
    H = gdxcc.new_gdxHandle_tp()
    _rc = gdxcc.gdxCreateD(H,finder.gams_dir,gdxcc.GMS_SSSIZE)
    gdxcc.gdxFree(H)
    load_specials(finder)
    return

try:
    load_gdxcc()
except:
    from gdxpds.tools import GamsDirFinder
    gams_dir = None
    try:
        gams_dir = GamsDirFinder().gams_dir
    except: pass
    logger.warning(f"Unable to load gdxcc with default GAMS directory '{gams_dir}'. "
                   "You may need to explicitly call gdxpds.load_gdxcc(gams_dir) "
                   "before importing pandas to avoid a library conflict.")


from gdxpds.read_gdx import to_dataframes, list_symbols, to_dataframe, get_data_types
from gdxpds.write_gdx import to_gdx
