import argparse
import os
import warnings

# gdxpds needs to be imported before pandas to try to avoid library conflict on
# Linux that causes a segmentation fault.
import gdxpds

import pandas as pd


def convert_csv_to_gdx(input_files, output_file, gams_dir=None):
    # check input files
    for ifile in input_files:
        if not os.path.splitext(ifile)[1] in ['.csv','.txt']:
            msg = "Input file '{}' is of unexpected type. Expected .csv or .txt.".format(ifile)
            raise RuntimeError(msg)
        if not os.path.isfile(ifile):
            raise RuntimeError("'{}' is not a file.".format(ifile))

    # convert input_files into one list of csvs
    ifiles = []
    for ifile in input_files:
        if os.path.splitext(ifile)[1] == '.csv':
            ifiles.append(ifile)
        else:
            # must be .txt
            f = open(ifile, 'r')
            for line in f:
                if not line == '':
                    if os.path.splitext(line.strip())[1] == '.csv':
                        ifiles.append(line.strip())
                    else:
                        print("Skipping '{}' found in '{}'.".format(line,ifile))
            f.close()
    if len(ifiles) == 0:
        raise RuntimeError("Nothing to convert.")

    # convert list of csvs to map of dataframes
    dataframes = {}
    for ifile in ifiles:
        dataframes[os.path.splitext(os.path.basename(ifile))[0]] = \
            pd.read_csv(ifile, index_col=None)

    gdxpds.to_gdx(dataframes, output_file, gams_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description='''Accepts one or more input
        csv files as input. Writes each csv as a separate symbol to an output
        gdx.''')
    parser.add_argument('-i', '--input', nargs='+', help='''List one or more
        .csv or .txt files. The latter are assumed to be a line-delimited list
        of .csv files.''')
    parser.add_argument('-o', '--output', default='export.gdx', help='''Path
        to the output gdx file. Will be overwritten if it already exists.''')
    parser.add_argument('-g', '--gams_dir', help='''Path to GAMS installation
        directory.''', default=None)

    args = parser.parse_args(argv)

    convert_csv_to_gdx(args.input, args.output, args.gams_dir)


def main_py_alias(argv=None):
    warnings.warn(
        "The 'csv_to_gdx.py' command is deprecated; use 'csv_to_gdx' (no .py). "
        "This alias will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    return main(argv)


if __name__ == "__main__":
    main()
