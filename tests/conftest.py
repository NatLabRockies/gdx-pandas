import os
import shutil
import subprocess

import pytest

import gdxpds.gdx


def pytest_addoption(parser):
    parser.addoption(
        "--no-clean-up",
        action="store_true",
        default=False,
        help="Pass this option to leave test outputs in place",
    )


@pytest.fixture(scope="session")
def clean_up(request):
    return not request.config.getoption("--no-clean-up")


@pytest.fixture(scope="session")
def base_dir():
    return os.path.dirname(__file__)


@pytest.fixture(scope="session")
def data_dir(base_dir):
    return os.path.join(base_dir, "data")


@pytest.fixture(scope="session")
def run_dir(base_dir):
    return os.path.join(base_dir, "output")


@pytest.fixture(scope="session", autouse=True)
def manage_rundir(request, clean_up, run_dir):
    if os.path.exists(run_dir):
        shutil.rmtree(run_dir)
    os.mkdir(run_dir)

    def finalize_rundir():
        if os.path.exists(run_dir) and clean_up:
            shutil.rmtree(run_dir)

    request.addfinalizer(finalize_rundir)


@pytest.fixture
def roundtrip_one_gdx(data_dir, run_dir):
    """Factory: returns a callable(filename, dirname) -> roundtripped_gdx_path.

    CLI scripts are invoked by entry-point name. pip places `csv_to_gdx` and
    `gdx_to_csv` on PATH after `pip install -e .`. Using subprocess (rather
    than direct in-process calls) keeps each round-trip in a fresh process.
    """

    def _roundtrip(filename, dirname):
        gdx_file = os.path.join(data_dir, filename)
        with gdxpds.gdx.GdxFile() as gdx:
            gdx.read(gdx_file)
            num_records = {}
            total_records = 0
            for symbol in gdx:
                num_records[symbol.name] = symbol.num_records
                total_records += num_records[symbol.name]
            assert total_records > 0

        out_dir = os.path.join(run_dir, dirname, os.path.splitext(filename)[0])
        if not os.path.exists(os.path.dirname(out_dir)):
            os.mkdir(os.path.dirname(out_dir))
        subprocess.run(["gdx_to_csv", "-i", gdx_file, "-o", out_dir], check=True)

        txt_file = os.path.join(out_dir, "csvs.txt")
        with open(txt_file, "w") as f:
            for p, _dirs, files in os.walk(out_dir):
                for file in files:
                    if os.path.splitext(file)[1] == ".csv":
                        f.write(f"{os.path.join(p, file)}\n")
                break
        roundtripped_gdx = os.path.join(out_dir, "output.gdx")
        subprocess.run(["csv_to_gdx", "-i", txt_file, "-o", roundtripped_gdx], check=True)

        with gdxpds.gdx.GdxFile(lazy_load=True) as gdx:
            gdx.read(roundtripped_gdx)
            for symbol_name, records in num_records.items():
                if records > 0:
                    assert symbol_name in gdx, f"Expected {symbol_name} in {roundtripped_gdx}."
                    assert gdx[symbol_name].num_records == records, (
                        f"Expected {symbol_name} in {roundtripped_gdx} to have {records} records, but has {gdx[symbol_name].num_records}."
                    )
        with gdxpds.gdx.GdxFile(lazy_load=False) as gdx:
            gdx.read(roundtripped_gdx)
            for symbol_name, records in num_records.items():
                if records > 0:
                    assert symbol_name in gdx, f"Expected {symbol_name} in {roundtripped_gdx}."
                    assert gdx[symbol_name].num_records == records, (
                        f"Expected {symbol_name} in {roundtripped_gdx} to have {records} records, but has {gdx[symbol_name].num_records}."
                    )

        return roundtripped_gdx

    return _roundtrip
