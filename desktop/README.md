# clasharness desktop app

Home for a native desktop app for clasharness.

The harness already serves its live UI at `http://localhost:8765` (see
`src/clasharness/server.py` and `src/clasharness/web/`). The desktop app is
expected to wrap that UI in a native window and manage the harness process,
so you don't need a terminal and browser tab to play.

Nothing is here yet: the framework (e.g. pywebview, Tauri or Electron) is
still to be chosen.
