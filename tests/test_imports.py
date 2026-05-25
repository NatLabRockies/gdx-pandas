"""Importing gdxpds must not require a GAMS binding, and the GMS_* type codes
hardcoded in the enums must match the live binding when one is installed."""

import subprocess
import sys

import pytest

import gdxpds.gdx as gdx

try:
    from gams.core import gdx as gdxcc
except ImportError:
    try:
        import gdxcc
    except ImportError:
        gdxcc = None


def test_import_pulls_no_binding_at_module_load():
    # A fresh interpreter importing gdxpds must not import a gdxcc binding at
    # module load -- the bindings are deferred to the first GDX operation.
    code = (
        "import sys, gdxpds\n"
        "pulled = [m for m in ('gdxcc', 'gams.core.gdx', 'gams.transfer') if m in sys.modules]\n"
        "assert not pulled, pulled\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


@pytest.mark.skipif(gdxcc is None, reason="no gdxcc binding installed")
def test_gms_constants_match_gdxcc():
    # The enum values are hardcoded so import needs no binding; verify they match
    # the live GMS_* constants whenever a binding is available.
    assert gdx.GamsDataType.Set.value == gdxcc.GMS_DT_SET
    assert gdx.GamsDataType.Parameter.value == gdxcc.GMS_DT_PAR
    assert gdx.GamsDataType.Variable.value == gdxcc.GMS_DT_VAR
    assert gdx.GamsDataType.Equation.value == gdxcc.GMS_DT_EQU
    assert gdx.GamsDataType.Alias.value == gdxcc.GMS_DT_ALIAS

    assert gdx.GamsVariableType.Unknown.value == gdxcc.GMS_VARTYPE_UNKNOWN
    assert gdx.GamsVariableType.Binary.value == gdxcc.GMS_VARTYPE_BINARY
    assert gdx.GamsVariableType.Integer.value == gdxcc.GMS_VARTYPE_INTEGER
    assert gdx.GamsVariableType.Positive.value == gdxcc.GMS_VARTYPE_POSITIVE
    assert gdx.GamsVariableType.Negative.value == gdxcc.GMS_VARTYPE_NEGATIVE
    assert gdx.GamsVariableType.Free.value == gdxcc.GMS_VARTYPE_FREE
    assert gdx.GamsVariableType.SOS1.value == gdxcc.GMS_VARTYPE_SOS1
    assert gdx.GamsVariableType.SOS2.value == gdxcc.GMS_VARTYPE_SOS2
    assert gdx.GamsVariableType.Semicont.value == gdxcc.GMS_VARTYPE_SEMICONT
    assert gdx.GamsVariableType.Semiint.value == gdxcc.GMS_VARTYPE_SEMIINT

    assert gdx.GamsEquationType.Equality.value == 53 + gdxcc.GMS_EQUTYPE_E
    assert gdx.GamsEquationType.GreaterThan.value == 53 + gdxcc.GMS_EQUTYPE_G
    assert gdx.GamsEquationType.LessThan.value == 53 + gdxcc.GMS_EQUTYPE_L
    assert gdx.GamsEquationType.NothingEnforced.value == 53 + gdxcc.GMS_EQUTYPE_N
    assert gdx.GamsEquationType.External.value == 53 + gdxcc.GMS_EQUTYPE_X
    assert gdx.GamsEquationType.Conic.value == 53 + gdxcc.GMS_EQUTYPE_C

    assert gdx.GamsValueType.Level.value == gdxcc.GMS_VAL_LEVEL
    assert gdx.GamsValueType.Marginal.value == gdxcc.GMS_VAL_MARGINAL
    assert gdx.GamsValueType.Lower.value == gdxcc.GMS_VAL_LOWER
    assert gdx.GamsValueType.Upper.value == gdxcc.GMS_VAL_UPPER
    assert gdx.GamsValueType.Scale.value == gdxcc.GMS_VAL_SCALE
