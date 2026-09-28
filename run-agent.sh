#!/usr/bin/env bash
# AI Code Engineer - Linux / macOS Launcher

set -e

# Change directory to script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1
export PYTHONDONTWRITEBYTECODE=1

# Detect Python interpreter (3.11+)
PYTHON_BIN=""

if [ -f ".venv/bin/python" ]; then
    if .venv/bin/python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
        PYTHON_BIN=".venv/bin/python"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 &>/dev/null; then
        if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
            PYTHON_BIN="python3"
        fi
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    if command -v python &>/dev/null; then
        if python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
            PYTHON_BIN="python"
        fi
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ Error: Python 3.11 or newer is required."
    echo "Please install Python 3.11+ using your system package manager (e.g. sudo apt install python3 python3-tk)."
    exit 1
fi

echo "🚀 Starting AI Code Engineer with $PYTHON_BIN..."
exec "$PYTHON_BIN" desktop.pyw "$@"
