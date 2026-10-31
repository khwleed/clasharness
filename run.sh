#!/usr/bin/env bash
# One-command launcher: sets things up on first run, then starts the harness.
#   ./run.sh                 start the app (UI in your browser)
#   ./run.sh desktop         start it as a desktop app, with the phone screen inside
#   ./run.sh check           check your Clash Royale API key and tag
#   ./run.sh crawl -n 300    build the opponent-prediction dataset
set -euo pipefail

# Follow symlinks (e.g. a `clasharness` link in ~/.local/bin) back to the repo.
self=$0
while [ -L "$self" ]; do
  link=$(readlink "$self")
  case $link in /*) self=$link ;; *) self=$(dirname "$self")/$link ;; esac
done
cd "$(dirname "$self")"

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
    uv pip install --quiet --python .venv/bin/python -e '.[desktop]'
  else
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e '.[desktop]'
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
if [ $# -eq 0 ] && command -v adb >/dev/null 2>&1 && ! adb devices | grep -q $'\tdevice$'; then
  echo "Warning: no phone found by adb. Plug it in, unlock it and accept USB debugging." >&2
fi

# The macOS hotkeys use Ctrl. If this window has focus, the terminal would grab Ctrl+Z
# (suspend), Ctrl+Y (delayed suspend) and Ctrl+S/Q (pause output) first, so switch
# those off while the app runs. Ctrl+C still stops it.
if [ -t 0 ]; then
  saved_tty=$(stty -g)
  trap 'stty "$saved_tty"' EXIT
  stty -ixon susp undef dsusp undef 2>/dev/null || stty -ixon susp undef
fi

if [ "${1:-}" = desktop ]; then
  shift
  .venv/bin/python desktop/app.py "$@"
else
  .venv/bin/python -m clasharness "$@"
fi
