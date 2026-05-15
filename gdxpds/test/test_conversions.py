import os
import subprocess as subp
import sys

import gdxpds.gdx
from gdxpds.test import base_dir, run_dir
from gdxpds.test.test_session import manage_rundir

import pandas as pd

def roundtrip_one_gdx(filename,dirname):
    # load gdx, make map of symbols and number of records
    gdx_file = os.path.join(base_dir,filename)
    with gdxpds.gdx.GdxFile() as gdx:
        gdx.read(gdx_file)
        num_records = {}
        total_records = 0
        for symbol in gdx:
            num_records[symbol.name] = symbol.num_records
            total_records += num_records[symbol.name]
        assert total_records > 0

    # call command-line interface to transform gdx to csv
    out_dir = os.path.join(run_dir, dirname, os.path.splitext(filename)[0])
    if not os.path.exists(os.path.dirname(out_dir)):
        os.mkdir(os.path.dirname(out_dir))
    cmds = [sys.executable, os.path.join(gdxpds.test.bin_prefix,'gdx_to_csv.py'),
            '-i', gdx_file,
            '-o', out_dir]
    subp.call(cmds)            

    # call command-line interface to transform csv to gdx
    txt_file = os.path.join(out_dir, 'csvs.txt')
    f = open(txt_file, 'w')
    for p, _dirs, files in os.walk(out_dir):
        for file in files:
            if os.path.splitext(file)[1] == '.csv':
                f.write("{}\n".format(os.path.join(p,file)))
        break
    f.close()
    roundtripped_gdx = os.path.join(out_dir, 'output.gdx')
    cmds = [sys.executable, os.path.join(gdxpds.test.bin_prefix,'csv_to_gdx.py'),
            '-i', txt_file,
            '-o', roundtripped_gdx]
    subp.call(cmds)

    # load gdx and check symbols and records against original map...
    # ... first without full load
    with gdxpds.gdx.GdxFile(lazy_load=True) as gdx:
        gdx.read(roundtripped_gdx)
        for symbol_name, records in num_records.items():
            if records > 0:
                assert symbol_name in gdx, "Expected {} in {}.".format(symbol_name,roundtripped_gdx)
                assert gdx[symbol_name].num_records == records, "Expected {} in {} to have {} records, but has {}.".format(symbol_name,roundtripped_gdx,records,gdx[symbol_name].num_records)
    # ... then with a full load
    with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
        gdx.read(roundtripped_gdx)
        for symbol_name, records in num_records.items():
            if records > 0:
                assert symbol_name in gdx, "Expected {} in {}.".format(symbol_name,roundtripped_gdx)
                assert gdx[symbol_name].num_records == records, "Expected {} in {} to have {} records, but has {}.".format(symbol_name,roundtripped_gdx,records,gdx[symbol_name].num_records)

    return roundtripped_gdx

        
def test_gdx_roundtrip(manage_rundir):
    filenames = ['CONVqn.gdx','OptimalCSPConfig_In.gdx','OptimalCSPConfig_Out.gdx']

    for filename in filenames:
        roundtrip_one_gdx(filename,'gdx_roundtrip')

    return
    
    
def test_csv_roundtrip(manage_rundir):
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
    cmds = [sys.executable, os.path.join(gdxpds.test.bin_prefix,'csv_to_gdx.py'),
            '-i', csvs[0], csvs[1],
            '-o', gdx_file]
    subp.call(cmds)
    
    # call command-line interface to transform gdx to csv
    cmds = [sys.executable, os.path.join(gdxpds.test.bin_prefix,'gdx_to_csv.py'),
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
    