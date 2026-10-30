#!/usr/bin/env bash
# One-command launcher: sets things up on first run, then starts the harness.
#   ./run.sh                 start the app
#   ./run.sh check           check your Clash Royale API key and tag
#   ./run.sh crawl -n 300    build the opponent-prediction dataset
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  for py in python3 python3.13 python3.12 python3.11; do
    if command -v "$py" >/dev/null 2>&1 &&
       "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      echo "$py"
      return
    fi
  done
}

STAMP=.venv/.installed
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  if [ ! -x .venv/bin/python ]; then
    PY=$(find_python)
    if [ -z "$PY" ]; then
      echo "Python 3.11+ is required. Install it with 'brew install python' (macOS)" >&2
      echo "or 'sudo apt install python3 python3-venv' (Linux), then run ./run.sh again." >&2
      exit 1
    fi
    echo "Creating virtual environment with $PY…"
    "$PY" -m venv .venv
  fi
  echo "Installing clasharness…"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --quiet --python .venv/bin/python -e .
  else
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e .
  fi
  touch "$STAMP"
fi

if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  cat <<'EOF'

Created config.toml. Open it and fill in:
  [api]        token   - your Clash Royale API key (whitelist the RoyaleAPI proxy IP, see README)
               my_tag  - your player tag, e.g. "#ABC123"
  [openrouter] api_key - your OpenRouter key (for reading the opponent's name)

Then run ./run.sh check to test the key, ./run.sh crawl -n 300 to build the
prediction data, and ./run.sh to start the app.
EOF
  exit 0
fi

if [ "$(uname)" = "Darwin" ]; then
  install_hint="brew install scrcpy android-platform-tools"
else
  install_hint="sudo apt install scrcpy adb"
fi
for tool in scrcpy adb; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Warning: '$tool' not found, so phone mirroring/screenshots won't work. Install with: $install_hint" >&2
  fi
done

exec .venv/bin/python -m clasharness "$@"
