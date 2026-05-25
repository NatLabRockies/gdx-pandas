import argparse
import os

import pandas as pd

import gdxpds


def convert_csv_to_gdx(input_files, output_file, gams_dir=None, backend=None):
    # check input files
    for ifile in input_files:
        if os.path.splitext(ifile)[1] not in [".csv", ".txt"]:
            msg = f"Input file '{ifile}' is of unexpected type. Expected .csv or .txt."
            raise RuntimeError(msg)
        if not os.path.isfile(ifile):
            raise RuntimeError(f"'{ifile}' is not a file.")

    # convert input_files into one list of csvs
    ifiles = []
    for ifile in input_files:
        if os.path.splitext(ifile)[1] == ".csv":
            ifiles.append(ifile)
        else:
            # must be a .txt file listing one CSV path per line
            with open(ifile) as f:
                for line in f:
                    entry = line.strip()
                    if not entry:
                        continue
                    if os.path.splitext(entry)[1] == ".csv":
                        ifiles.append(entry)
                    else:
                        print(f"Skipping '{entry}' found in '{ifile}'.")
    if len(ifiles) == 0:
        raise RuntimeError("Nothing to convert.")

    # convert list of csvs to map of dataframes
    dataframes = {}
    for ifile in ifiles:
        dataframes[os.path.splitext(os.path.basename(ifile))[0]] = pd.read_csv(
            ifile, index_col=None
        )

    gdxpds.to_gdx(dataframes, output_file, gams_dir, backend=backend)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="""Accepts one or more input
        csv files as input. Writes each csv as a separate symbol to an output
        gdx."""
    )
    parser.add_argument(
        "-i",
        "--input",
        nargs="+",
        help="""List one or more
        .csv or .txt files. The latter are assumed to be a line-delimited list
        of .csv files.""",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="export.gdx",
        help="""Path
        to the output gdx file. Will be overwritten if it already exists.""",
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

    convert_csv_to_gdx(args.input, args.output, args.gams_dir, args.backend)


if __name__ == "__main__":
    main()
