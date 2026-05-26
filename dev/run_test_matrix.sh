#!/usr/bin/env bash
# Run pytest + `gdxpds test` across the GAMS matrix described in dev/README.md.
#
# Invoke from an interactive bash shell (needs the `module` function):
#     bash dev/run_test_matrix.sh
#
# For each existing .venv-* below, this script:
#   1) sources its bin/activate (which should `module load gams/<ver>` and
#      pin GAMS_DIR per the patches in dev/README.md);
#   2) runs `pytest tests`;
#   3) runs `gdxpds info` (binding-free diagnostic, must succeed in every venv
#      including .venv-no-gams since v3.0.0 made the import binding-free);
#   4) runs `gdxpds test`;
#   5) in .venv-no-gams only, additionally runs `pip wheel --no-deps .` to
#      confirm the wheel still builds without GAMS bindings (guards the
#      static-attr `version` read in pyproject.toml);
#   6) deactivates.
#
# Per-venv logs go to dev/test_matrix_logs/<venv>.log; a top-level
# summary is printed to stdout and saved to dev/test_matrix_logs/summary.txt.
#
# For .venv-no-gams, pytest and gdxpds test are expected to FAIL cleanly
# (non-zero exit, no segfault, useful error message); `gdxpds info` and the
# wheel build are expected to SUCCEED. The script flips its verdict accordingly.

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

LOG_DIR="$REPO_ROOT/dev/test_matrix_logs"
mkdir -p "$LOG_DIR"
SUMMARY="$LOG_DIR/summary.txt"
: > "$SUMMARY"

VENVS=(
    ".venv-gams-34"
    ".venv-gams-49"
    ".venv-gams-51"
    ".venv-no-gams"
)

run_one_venv () {
    local venv="$1"
    local log="$LOG_DIR/${venv#.}.log"
    : > "$log"

    if [ ! -d "$REPO_ROOT/$venv" ]; then
        printf "%-20s  SKIPPED (venv not found)\n" "$venv" | tee -a "$SUMMARY"
        return
    fi

    # Activation scripts use unset vars; tolerate that.
    set +u
    # shellcheck disable=SC1090
    source "$REPO_ROOT/$venv/bin/activate"
    set -u

    {
        echo "=== $venv ==="
        echo "--- env ---"
        echo "GAMS_DIR=${GAMS_DIR:-<unset>}"
        echo "which gams: $(command -v gams 2>/dev/null || echo '<none>')"
        echo "python:     $(command -v python)"
        python -c "import sys; print('python version:', sys.version.split()[0])"
        echo
    } | tee -a "$log"

    echo "--- pytest ---" | tee -a "$log"
    pytest tests >>"$log" 2>&1
    local pytest_rc=$?
    echo "pytest exit: $pytest_rc" | tee -a "$log"
    echo | tee -a "$log"

    # `gdxpds info` is the binding-free diagnostic introduced in v3.0.0: it
    # imports gdxpds without needing GAMS, reports what bindings are visible,
    # and is contracted to never raise (return code 0 even when nothing is
    # installed). Run it everywhere -- including .venv-no-gams.
    echo "--- gdxpds info ---" | tee -a "$log"
    gdxpds info >>"$log" 2>&1
    local info_rc=$?
    echo "gdxpds info exit: $info_rc" | tee -a "$log"
    echo | tee -a "$log"

    echo "--- gdxpds test ---" | tee -a "$log"
    gdxpds test >>"$log" 2>&1
    local gdxpds_rc=$?
    echo "gdxpds test exit: $gdxpds_rc" | tee -a "$log"
    echo | tee -a "$log"

    local wheel_rc=0
    if [ "$venv" = ".venv-no-gams" ]; then
        echo "--- pip wheel (no-bindings build smoke) ---" | tee -a "$log"
        local wheel_out="$LOG_DIR/wheel-no-gams"
        rm -rf "$wheel_out"
        pip wheel --no-deps -w "$wheel_out" "$REPO_ROOT" >>"$log" 2>&1
        wheel_rc=$?
        if [ "$wheel_rc" -eq 0 ] && ! ls "$wheel_out"/*.whl >/dev/null 2>&1; then
            wheel_rc=1
        fi
        echo "pip wheel exit: $wheel_rc" | tee -a "$log"
        echo | tee -a "$log"
    fi

    local verdict
    if [ "$venv" = ".venv-no-gams" ]; then
        if [ "$pytest_rc" -ne 0 ] && [ "$gdxpds_rc" -ne 0 ] \
           && [ "$info_rc" -eq 0 ] && [ "$wheel_rc" -eq 0 ]; then
            verdict="OK (info+wheel succeed; pytest/gdxpds fail as expected)"
        else
            verdict="UNEXPECTED (pytest=$pytest_rc, info=$info_rc, gdxpds=$gdxpds_rc, wheel=$wheel_rc)"
        fi
    else
        if [ "$pytest_rc" -eq 0 ] && [ "$info_rc" -eq 0 ] && [ "$gdxpds_rc" -eq 0 ]; then
            verdict="PASS"
        else
            verdict="FAIL (pytest=$pytest_rc, info=$info_rc, gdxpds=$gdxpds_rc)"
        fi
    fi
    echo "verdict: $verdict" | tee -a "$log"

    printf "%-20s  %s  (log: %s)\n" "$venv" "$verdict" "$log" >> "$SUMMARY"

    set +u
    deactivate >/dev/null 2>&1 || true
    set -u
}

set -u
for venv in "${VENVS[@]}"; do
    run_one_venv "$venv"
done

echo
echo "=== summary ==="
cat "$SUMMARY"
