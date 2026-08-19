#!/usr/bin/env bash

set -euo pipefail

# Always run relative to this script, regardless of the terminal's directory.
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"

cd "$PROJECT_DIR"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    echo "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Install dependencies on first launch. Remove this marker to force a reinstall.
if [[ ! -f "$VENV_DIR/.dashboard-dependencies-installed" ]]; then
    echo "Installing dashboard dependencies..."
    "$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"
    touch "$VENV_DIR/.dashboard-dependencies-installed"
fi

exec "$VENV_DIR/bin/python" -m fsae_dashboard "$@"
