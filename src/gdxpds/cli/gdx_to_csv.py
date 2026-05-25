import argparse
import logging
import os

import gdxpds

logger = logging.getLogger(__name__)


def convert_gdx_to_csv(in_gdx, out_dir, gams_dir=None, backend=None):
    # check inputs
    if not os.path.exists(os.path.dirname(out_dir)):
        raise RuntimeError(f"Parent directory of output directory '{out_dir}' does not exist.")

    # convert to pandas.DataFrames
    dataframes = gdxpds.to_dataframes(in_gdx, gams_dir, backend=backend)

    # write to files
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)

    for symbol_name, df in dataframes.items():
        csv_path = os.path.join(out_dir, symbol_name + ".csv")
        if os.path.exists(csv_path):
            logger.info(f"Overwriting '{csv_path}'")
        df.to_csv(csv_path, na_rep="NaN", index=False)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="""Reads a gdx file into
        pandas dataframes, and then writes them out as csv files."""
    )
    parser.add_argument(
        "-i",
        "--in_gdx",
        help="""Input gdx file to be read
                        and exported as one csv per symbol.""",
    )
    parser.add_argument(
        "-o",
        "--out_dir",
        default="./gdx_data/",
        help="""Directory to which csvs are to be written.""",
    )
    parser.add_argument(
        "-g",
        "--gams_dir",
        help="""Path to GAMS installation
                        directory.""",
        default=None,
    )
    parser.add_argument(
        "-b",
        "--backend",
        choices=[b.value for b in gdxpds.Backend],
        default=None,
        help="""I/O engine to use. Defaults to the GDXPDS_BACKEND environment
                        variable, then 'gams_transfer' when usable, otherwise 'gdxcc'.""",
    )

    args = parser.parse_args(argv)

    convert_gdx_to_csv(args.in_gdx, os.path.realpath(args.out_dir), args.gams_dir, args.backend)


if __name__ == "__main__":
    main()
