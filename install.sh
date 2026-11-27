#!/usr/bin/env bash
# ReelCut installer (Linux / macOS)
# 1. finds Python 3.11+  2. creates .venv + installs  3. guided first setup
set -u

cd "$(dirname "$0")" || exit 2
echo
echo "============================================================"
echo "  ReelCut - install"
echo "  folder: $(pwd)"
echo "============================================================"
echo

PY=""
for cand in python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)' >/dev/null 2>&1; then
            PY="$cand"
            break
        fi
    fi
done
if [ -z "$PY" ]; then
    echo "[x] Python 3.11+ not found."
    echo "    Debian/Ubuntu: sudo apt-get install -y python3 python3-venv python3-pip"
    echo "    macOS:         brew install python@3.12"
    exit 2
fi
echo "[1/4] Python $("$PY" -c 'import sys;print(sys.version.split()[0])')"

VPY=".venv/bin/python"
if [ -x "$VPY" ]; then
    echo "[2/4] virtual env: already present"
else
    echo "[2/4] creating .venv ..."
    "$PY" -m venv .venv || { echo "[x] could not create .venv (install python3-venv)"; exit 2; }
fi

export PYTHONIOENCODING=utf-8
echo "[3/4] installing dependencies (first run downloads a few hundred MB) ..."
if ! "$VPY" -m pip --version >/dev/null 2>&1; then
    echo "      preparing pip ..."
    "$VPY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! "$VPY" -m pip --version >/dev/null 2>&1; then
    if command -v uv >/dev/null 2>&1; then
        echo "      using uv instead of pip ..."
        UV_OK=1
    else
        echo "[x] pip is missing in .venv and could not be prepared."
        echo "    Delete .venv and run ./install.sh again (apt install python3-venv)."
        exit 2
    fi
fi
"$VPY" -m pip install --upgrade pip --quiet 2>/dev/null || true
if [ "${UV_OK:-0}" = "1" ]; then
    uv pip install --python "$VPY" -e "." || { echo "[x] Dependency install failed."; exit 2; }
elif ! "$VPY" -m pip install -e "."; then
    echo "[x] Dependency install failed. Check the internet connection and retry;"
    echo "    if it persists, copy the last 20 lines of this output into an issue."
    exit 2
fi
echo "      installed"

echo "[4/4] first setup"
"$VPY" -m reelcut.firstrun
rc=$?
if [ "$rc" -eq 3 ]; then
    echo
    echo "[!] Setup needs one more step - the notes above say which."
    echo "    Run it again later:  .venv/bin/reelcut setup"
fi

echo
echo "============================================================"
echo "  Done. Useful commands:"
echo "    .venv/bin/reelcut status"
echo "    .venv/bin/reelcut transcribe-gemini \"path/to/project\""
echo "    .venv/bin/reelcut brolls \"path/to/project\""
echo "============================================================"
echo
