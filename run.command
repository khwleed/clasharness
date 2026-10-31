#!/bin/zsh
# Start clasharness. Double-click this in Finder, or run ./run.command from a terminal.
# Opens the UI at http://127.0.0.1:8765 and starts scrcpy. Press Ctrl-C to stop.

# ${0:A:h} resolves symlinks, so this also works when linked onto PATH.
cd "${0:A:h}" || exit 1

if [[ ! -x .venv/bin/python ]]; then
  echo "No .venv here. Set it up first:  uv venv --python 3.13 && uv pip install -e \".[dev]\""
  read -k1 "?Press any key to close."
  exit 1
fi

if [[ ! -f config.toml ]]; then
  echo "No config.toml. Copy config.example.toml to config.toml and add your keys."
  read -k1 "?Press any key to close."
  exit 1
fi

# Warn (but carry on) if the phone isn't connected and authorized.
if ! adb devices | grep -q $'\tdevice$'; then
  echo "Warning: no phone found by adb. Plug it in, unlock it and accept USB debugging."
  echo
fi

# The Mac hotkeys use Ctrl. If this window has focus, the terminal would grab Ctrl+Z
# (suspend), Ctrl+Y (delayed suspend) and Ctrl+S/Q (pause output) first, so switch
# those off while the app runs. Ctrl-C still stops it.
if [[ -t 0 ]]; then
  saved_tty=$(stty -g)
  trap 'stty "$saved_tty"' EXIT
  stty -ixon susp undef dsusp undef
fi

.venv/bin/python -m clasharness "$@"
