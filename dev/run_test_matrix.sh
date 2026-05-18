#!/usr/bin/env bash
# Run pytest + `gdxpds test` across the GAMS matrix described in dev/README.md.
#
# Invoke from an interactive bash shell (needs the `module` function):
#     bash dev/run_test_matrix.sh
#
# For each existing .venv-* below, this script:
#   1) sources its bin/activate (which should `module load gams/<ver>` and
#      pin GAMS_DIR per the patches in dev/README.md);
#   2) runs `pytest tests` (normal order);
#   3) runs `GDXPDS_TEST_PREIMPORT_PANDAS=1 pytest tests` (historical bad
#      order: pandas imported before gdxpds; see tests/conftest.py);
#   4) runs `gdxpds test`;
#   5) deactivates.
#
# Per-venv logs go to dev/test_matrix_logs/<venv>.log; a top-level
# summary is printed to stdout and saved to dev/test_matrix_logs/summary.txt.
#
# For .venv-no-gams every command is expected to FAIL cleanly (non-zero
# exit, no segfault, useful error message). The script flips its verdict
# accordingly.

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

    echo "--- pytest (normal order) ---" | tee -a "$log"
    pytest tests >>"$log" 2>&1
    local pytest_rc=$?
    echo "pytest exit: $pytest_rc" | tee -a "$log"
    echo | tee -a "$log"

    echo "--- pytest with GDXPDS_TEST_PREIMPORT_PANDAS=1 ---" | tee -a "$log"
    GDXPDS_TEST_PREIMPORT_PANDAS=1 pytest tests >>"$log" 2>&1
    local pytest_preimport_rc=$?
    echo "pytest (preimport) exit: $pytest_preimport_rc" | tee -a "$log"
    echo | tee -a "$log"

    echo "--- gdxpds test ---" | tee -a "$log"
    gdxpds test >>"$log" 2>&1
    local gdxpds_rc=$?
    echo "gdxpds test exit: $gdxpds_rc" | tee -a "$log"
    echo | tee -a "$log"

    local verdict
    if [ "$venv" = ".venv-no-gams" ]; then
        if [ "$pytest_rc" -ne 0 ] && [ "$pytest_preimport_rc" -ne 0 ] && [ "$gdxpds_rc" -ne 0 ]; then
            verdict="OK (all 3 failed as expected for no-GAMS)"
        else
            verdict="UNEXPECTED (no-GAMS venv had a passing command; pytest=$pytest_rc, preimport=$pytest_preimport_rc, gdxpds=$gdxpds_rc)"
        fi
    else
        if [ "$pytest_rc" -eq 0 ] && [ "$pytest_preimport_rc" -eq 0 ] && [ "$gdxpds_rc" -eq 0 ]; then
            verdict="PASS"
        else
            verdict="FAIL (pytest=$pytest_rc, preimport=$pytest_preimport_rc, gdxpds=$gdxpds_rc)"
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
