import os
import subprocess as subp
import sys

import gdxpds.gdx

import pandas as pd


def test_gdx_roundtrip(roundtrip_one_gdx):
    filenames = ['CONVqn.gdx','OptimalCSPConfig_In.gdx','OptimalCSPConfig_Out.gdx']

    for filename in filenames:
        roundtrip_one_gdx(filename,'gdx_roundtrip')


def test_csv_roundtrip(base_dir, run_dir, bin_prefix):
    # load csvs into pandas and make map of filenames to number of rows
    csvs = [os.path.join(base_dir, 'installed_capacity.csv'),
            os.path.join(base_dir, 'annual_generation.csv')]
    n = len(csvs)
    num_records = {}
    total_records = 0
    for csv in csvs:
        df = pd.read_csv(csv, index_col = None)
        num_records[os.path.splitext(os.path.basename(csv))[0]] = len(df.index)
        total_records += len(df.index)
    assert total_records > 0

    # call command-line interface to transform csv to gdx
    out_dir = os.path.join(run_dir, 'csv_roundtrip')
    if not os.path.exists(out_dir):
        os.mkdir(out_dir)
    gdx_file = os.path.join(out_dir, 'intermediate.gdx')
    cmds = [sys.executable, os.path.join(bin_prefix,'csv_to_gdx.py'),
            '-i', csvs[0], csvs[1],
            '-o', gdx_file]
    subp.call(cmds)

    # call command-line interface to transform gdx to csv
    cmds = [sys.executable, os.path.join(bin_prefix,'gdx_to_csv.py'),
            '-i', gdx_file,
            '-o', out_dir]
    subp.call(cmds)

    # load csvs into pandas and check filenames and number of rows against original map
    for csv_name, records in num_records.items():
        csv_file = os.path.join(out_dir, csv_name + '.csv')
        assert os.path.isfile(csv_file)
        df = pd.read_csv(csv_file, index_col = None)
        assert len(df.index) == records

    cnt = 0
    for _p, _dirs, files in os.walk(out_dir):
        for file in files:
            if os.path.splitext(file)[1] == '.csv':
                cnt += 1
        break
    assert cnt == n
