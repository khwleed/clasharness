#!/bin/zsh
# Double-click in Finder to start clasharness (opens in Terminal). Same as ./run.sh.
exec "${0:A:h}/run.sh" "$@"
