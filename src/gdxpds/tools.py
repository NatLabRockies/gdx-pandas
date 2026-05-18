import logging
import os
import subprocess as subp
import re

logger = logging.getLogger(__name__)


class Error(Exception):
    """
    Base class for all Exceptions raised by this package.
    """


class GamsLoadError(Error):
    """
    Raised when gdxpds cannot bring up the GAMS shared library at the
    resolved gams_dir (non-GAMS directory, gdxCreateD non-success return,
    version skew, missing shared library, etc.).
    """


def _require_gams_installation(finder):
    """Verify the resolved GAMS directory actually contains GAMS.

    Calling the SWIG-bound ``gdxCreateD`` against a directory that does not
    contain the GAMS shared library can crash the Python process on Windows
    (access violation). This pre-check raises :class:`GamsLoadError` first,
    naming the discovery branch that produced the bad directory.
    """
    gams_exe = "gams.exe" if os.name == "nt" else "gams"
    candidate = os.path.join(finder.gams_dir, gams_exe)
    if not os.path.exists(candidate):
        raise GamsLoadError(
            f"Resolved GAMS directory {finder.gams_dir!r} "
            f"[source: {finder.source}] does not contain {gams_exe!r}; "
            f"this is not a GAMS installation."
        )


def _check_gdx_create_rc(H, rc, gdxcc, gams_dir, source):
    """Raise :class:`GamsLoadError` if gdxCreateD failed.

    ``rc`` may be a bare int (older bindings) or a ``[code, msg]`` 2-list
    (modern ``gams.core.gdx``). Either way, ``code == 1`` is success.
    """
    if isinstance(rc, (list, tuple)):
        code = rc[0]
        msg = rc[1] if len(rc) > 1 else ''
    else:
        code = rc
        msg = ''
    if code == 1:
        return
    if not msg and hasattr(gdxcc, 'gdxErrorStr') and hasattr(gdxcc, 'gdxGetLastError'):
        try:
            msg = gdxcc.gdxErrorStr(None, gdxcc.gdxGetLastError(H))[1]
        except Exception:
            pass
    gdxcc.gdxFree(H)
    raise GamsLoadError(
        f"gdxCreateD failed (rc={code}) for GAMS directory {gams_dir!r} "
        f"[source: {source}]. gdxcc reported: {msg!r}"
    )


class GamsDirFinder(object):
    """
    Class for finding and accessing the system's GAMS directory. 

    The find function first looks for the 'GAMS_DIR' environment variable. If 
    that is unsuccessful, it next uses 'which gams' for POSIX systems, and the 
    default install location, 'C:/GAMS', for Windows systems. In the latter case
    it prefers the largest version number.
    
    You can always specify the GAMS directory directly, and this class will attempt 
    to clean up your input. (Even on Windows, the GAMS path must use '/' rather than 
    '\'.)
    """
    gams_dir_cache = None

    def __init__(self,gams_dir=None):
        self.__source = None
        self.gams_dir = gams_dir

    @property
    def source(self):
        """Label describing which discovery branch produced ``gams_dir``.

        Set whenever a non-None ``gams_dir`` is resolved. Useful for diagnosing
        "wrong GAMS directory got picked" reports. ``None`` if no GAMS directory
        could be resolved.
        """
        return self.__source

    @property
    def gams_dir(self):
        """The GAMS directory on this system."""
        if self.__gams_dir is None:
            raise RuntimeError("Unable to locate your GAMS directory.")
        return self.__gams_dir

    @gams_dir.setter
    def gams_dir(self, value):
        self.__gams_dir = None
        if isinstance(value, str):
            self.__gams_dir = self.__clean_gams_dir(value)
            if self.__gams_dir is not None:
                self.__source = "explicit override"
        elif value is not None:
            logger.warning(f"Unexpected gams_dir type {type(value)}. Ignoring "
                f"input {value!r} because it is not a str.")
        if self.__gams_dir is None:
            self.__gams_dir = self.__find_gams()
            
    def __find_gams_root_in(self, parent):
        """Search direct subdirectories of `parent` for a GAMS installation,
        identified by the presence of gams.exe. If multiple are found, prefer
        the one whose directory name parses as the largest version."""
        if not os.path.isdir(parent):
            return None
        roots = []
        try:
            for name in os.listdir(parent):
                d = os.path.join(parent, name)
                if os.path.isdir(d) and os.path.exists(os.path.join(d, 'gams.exe')):
                    roots.append(d)
        except OSError:
            return None
        if not roots:
            return None
        if len(roots) == 1:
            return roots[0]
        def _parse(d):
            name = os.path.basename(d)
            try:
                return (float(name),)
            except ValueError:
                pass
            try:
                return tuple(int(p) for p in name.split('.'))
            except ValueError:
                return None
        parsed = [(_parse(d), d) for d in roots]
        valid = [(v, d) for v, d in parsed if v is not None]
        if valid:
            return max(valid)[1]
        return roots[0]

    def __clean_gams_dir(self,value):
        """
        Cleans up the path string.
        """
        if value is None:
            return None
        assert(isinstance(value, str))
        ret = os.path.realpath(value)
        if not os.path.exists(ret):
            return None
        ret = re.sub('\\\\','/',ret)
        return ret
        
    def __find_gams(self):
        """
        For all systems, the first place we examine is the GAMS_DIR environment
        variable, and the second is GAMSDIR.

        For Windows, the next step is to try 'where gams'. Then we look in the 
        default install location (C:/GAMS), preferring win64 to win32 and the 
        most recent version.
        
        For all others, the next step is 'which gams'.
        
        Returns
        -------
        str or None
            If not None, the return value is the found gams_dir
        """
        # check for environment variable
        ret = os.environ.get('GAMS_DIR')
        ret = self.__clean_gams_dir(ret)
        if ret is not None:
            self.__source = "GAMS_DIR env var"

        if ret is None:
            ret = os.environ.get('GAMSDIR')
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "GAMSDIR env var"

        if ret is None and os.name == 'nt':
            # windows systems
            try:
                ret = os.path.dirname(subp.check_output(['where', 'gams']).decode().split("\n")[0])
            except:
                ret = None
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "where gams"

        if ret is None and os.name == 'nt':
            # search in default installation location. Modern GAMS (v42+)
            # installs to C:\GAMS\<version>\; legacy installs went to
            # C:\GAMS\win64\<version>\. A GAMS root is identified by the
            # presence of gams.exe.
            ret = self.__find_gams_root_in(r'C:\GAMS')
            if ret is not None:
                self.__source = r"C:\GAMS default-location walk (modern layout)"
            else:
                ret = self.__find_gams_root_in(os.path.join(r'C:\GAMS', 'win64'))
                if ret is not None:
                    self.__source = r"C:\GAMS\win64 default-location walk (legacy layout)"
            ret = self.__clean_gams_dir(ret)

        if ret is None and os.name != 'nt':
            # posix systems
            try:
                ret = os.path.dirname(subp.check_output(['which', 'gams'])).decode()
            except:
                ret = None
            ret = self.__clean_gams_dir(ret)
            if ret is not None:
                self.__source = "which gams"

        if ret is not None:
            GamsDirFinder.gams_dir_cache = ret

        if ret is None:
            logger.debug(f"Did not find GAMS directory. Using cached value {self.gams_dir_cache}.")
            ret = GamsDirFinder.gams_dir_cache
            if ret is not None:
                self.__source = "cached"

        return ret
        
class NeedsGamsDir(object):
    """Mix-in class that asserts that a GAMS directory is needed and provides the methods 
    required to find and access it."""

    def __init__(self,gams_dir=None):
        self.gams_dir = gams_dir
        
    @property
    def gams_dir(self):
        """
        The GAMS directory whose value has either been directly set or has been found using 
        the GamsDirFinder class.

        Returns
        -------
        str    
        """
        return self.__gams_dir
        
    @gams_dir.setter
    def gams_dir(self, value):
        self.__gams_dir = GamsDirFinder(value).gams_dir    

