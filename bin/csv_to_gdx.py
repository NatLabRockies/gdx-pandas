# Deprecated: use the `csv_to_gdx` command instead. This shim is preserved for
# interface compatibility in v1.5.0 and will be removed in a future release.
from gdxpds.cli.csv_to_gdx import main_py_alias

if __name__ == "__main__":
    main_py_alias()
