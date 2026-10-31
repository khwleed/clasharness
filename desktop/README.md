# clasharness desktop app

The harness UI and your phone's screen in one native window, with no browser tab and
no separate scrcpy window.

```bash
./run.sh desktop
```

`run.sh` installs [pywebview](https://pywebview.flowrl.com/) the first time. On macOS
the window uses the system WebKit, so nothing else is needed. Closing the window quits.

## Dock / Spotlight app (macOS)

```bash
desktop/make-mac-app.sh
```

This builds `~/Applications/Clash Harness.app` (pass another folder to put it
elsewhere). It runs `./run.sh desktop` from this checkout, so rebuild it if you move the
repo. Run `./run.sh` once in Terminal first so `config.toml` exists. Its output goes to
`var/desktop.log`.

The first time it runs, macOS asks for **Accessibility** and **Input Monitoring**
permission for the global hotkeys (System Settings → Privacy & Security).

## How it works

- `app.py` starts the normal clasharness server on a background thread and opens a
  pywebview window on it. Options: `--config PATH`, `-v`, and `--debug` (web inspector).
- The phone screen comes from `src/clasharness/phone.py` and `src/clasharness/web/phone.js`.
  They work the same in a browser, so `./run.sh` gets the embedded phone too.

## Requirements

- macOS 13.3 or newer (the window decodes the video with WebCodecs).
- scrcpy and adb: `brew install scrcpy android-platform-tools`.
- USB debugging on the phone (Samsung: Settings → About phone → Software information →
  tap Build number 7 times, then Developer options → USB debugging).
