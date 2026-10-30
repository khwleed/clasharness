# clasharness

A second-screen harness for playing Clash Royale from your computer:

- **Mirror and control your Android phone** with [scrcpy](https://github.com/Genymobile/scrcpy).
- **Track elixir for both players.** It follows the match clock (x1, then x2, then overtime x2/x3), the 10 elixir cap, leaked elixir, Mirror, champion abilities and Elixir Collector.
- **Track both hands and card cycles** from the cards you log. Your hand is known exactly after 4 plays. For the opponent it shows which cards they *can't* have right now.
- **Predict the opponent's unseen cards** from a dataset of real ranked decks, built from the official API. It also uses the opponent's own recent decks once it knows who they are.
- **Identify the opponent automatically.** At match start it takes a screenshot of the phone, a vision model on OpenRouter (DeepSeek V4.1 Flash by default) reads the opponent's name and clan, and the app finds their player tag. That works without a clan too, through a local player index.
- **Save every match** to SQLite and match it up with your battle log afterwards (result, crowns, the opponent's full deck).

Card logging is manual: one keypress per card, using global hotkeys that work while scrcpy has focus.

```
phone ──USB/Wi-Fi──> scrcpy window (you play here)
global hotkeys ─┐
browser UI ─────┼─> event log ─> elixir / cycle / prediction ─> live UI (localhost:8765)
search popup ───┘
Clash Royale API (via RoyaleAPI proxy) ─> decks, players, battle logs ─> SQLite
OpenRouter vision ─> opponent name ─> player tag ─> their recent decks
```

## Quick start

```bash
git clone https://github.com/khwleed/clasharness.git
```

```bash
cd clasharness && ./run.sh
```

The first run creates a virtual environment, installs the app and creates `config.toml`. Fill in your keys there (steps 2 and 3 below), then:

```bash
./run.sh check
```

```bash
./run.sh crawl -n 300
```

```bash
./run.sh
```

After that, `./run.sh` is all you need each time you play. Press **Ctrl+C** in the terminal to quit.

## Setup details

### 1. Prerequisites

You need Python 3.11+, `scrcpy` and `adb`:

```bash
brew install python scrcpy android-platform-tools   # macOS
```

```bash
sudo apt install python3 python3-venv python3-tk scrcpy adb   # Debian/Ubuntu
```

On the phone, turn on **Developer options → USB debugging**, plug it in and accept the prompt. Check that `scrcpy` on its own shows your screen first. `run.sh` warns you if `scrcpy` or `adb` is missing.

### 2. Clash Royale API key (via the RoyaleAPI proxy)

Developer keys are locked to IP addresses. Your home IP changes, so route through [RoyaleAPI's proxy](https://docs.royaleapi.com/proxy.html) instead:

1. Sign in at <https://developer.clashroyale.com> and create a key.
2. For the allowed IP address, enter the **RoyaleAPI proxy IP**. At the time of writing it's `45.79.218.79`; check their docs, because it has changed before.
3. In `config.toml`, set `token` and `my_tag`.
4. Check it with `./run.sh check`.

### 3. OpenRouter API key (for reading the opponent's name)

Create a key at <https://openrouter.ai/keys>. Put it in `config.toml` under `[openrouter]`, or export `OPENROUTER_API_KEY`. Each match makes one small image request to `deepseek/deepseek-v4.1-flash`, which costs a tiny fraction of a cent. You can set `model` to any OpenRouter model that accepts images. Avoid the `deepseek/deepseek-flash-latest` alias, which is text-only.

Without a key, everything else still works: paste the opponent's tag by hand, or skip it.

### 4. Build the prediction dataset

```bash
./run.sh crawl -n 500
```

This starts from your own battle log and walks outward through opponents near your trophy count, storing every ranked 1v1 deck and every player it sees. 500 players takes a few minutes at the default 5 requests per second. Add `--pol-top` to also seed from the Path of Legends leaderboard. While the app runs, it keeps adding a little more every 10 minutes (`[predict]` in config).

### 5. Run

```bash
./run.sh
```

This opens <http://127.0.0.1:8765> and, with `autostart = true`, starts scrcpy. Put the two windows side by side (or put the UI on a second monitor or a tablet on the same machine).

## Playing with it

| Key | Action |
|---|---|
| **F9** | Start the match. Press it on "Fight!", which starts the clock |
| **F1–F8** | You played deck card 1–8 (order shown in the UI) |
| **Alt+1…8** | Opponent played their 1st…8th *revealed* card |
| **Alt+Q/W/E/R** | Opponent played predicted card #1–4 (the list updates live) |
| **Alt+Space** | Search popup for any other opponent card |
| **F12** | Undo the last event |
| **F11** | Skip to the next elixir phase (if the timer drifted) |
| **F10** | End the match |
| Ctrl+= / Ctrl+- | Your elixir ±1 (correction, or Elixir Golem blobs) |
| Alt+= / Alt+- | Opponent elixir ±1 |
| Ctrl+A / Alt+A | Champion ability used (you / opponent) |
| Alt+I | Read the opponent's name from the screen again |

You can rebind any of these in `[hotkeys]`. Everything can also be clicked in the UI.

The opponent's first appearance of a card is the slow part. Usually it's in the predicted list (Alt+Q…R). If not, press Alt+Space, type a few letters and press Enter.

**Who is the opponent?** About 1.5 s after Start, the harness takes a screenshot through `adb` and crops the top banner. the vision model reads the name and clan, and the app looks for the tag in this order:

1. **Local player index.** Every player seen in any battle log or clan member list is stored here, so it works for players with no clan. It grows as you crawl and play.
2. **Clan search.** Search clans by the clan name, then match the name against members.
3. If neither finds them, the UI shows a **RoyaleAPI search link** for the name, and you can paste the tag.

Once a tag is known, their recent battle log is loaded and their own decks dominate the prediction. If the crop is off for your phone, check **Setup → Name crop calibration** and adjust `[vision].crop`.

After **End**, the harness waits about 20 s, finds the battle in your battle log, and saves the result, the crowns and the opponent's full deck (**Recent matches** in the UI).

## Platform notes

- **macOS:** grant your terminal **Accessibility** and **Input Monitoring** permission (System Settings → Privacy & Security) or global hotkeys won't fire. Function keys may need **Fn**, or turn on "Use F1, F2, etc. keys as standard function keys". Option+letter types special characters on macOS, which can stop `<alt>+q`-style bindings from matching. If that happens, rebind them to `<ctrl>+<shift>+…` or `<cmd>+…`.
- **Linux:** global hotkeys need an **X11** session (pynput can't hook Wayland). On Wayland, log in with an X11 session, or use the browser UI on a second screen or device.
- scrcpy forwards key presses to the phone as well. Clash Royale ignores F-keys and Alt combos, so this is harmless.

## How it works

- `match/elixir.py`: elixir as a pure function of time and events. The phase table is in config. Collector pumps, overspend detection (a sign the timer drifted) and leak tracking are included.
- `match/cycle.py`: the queue model. Unplayed cards are always ahead of played ones, and played cards are ordered by when they were last played, so the hand is exact once at most 4 cards are unplayed.
- `match/state.py`: the event log. The UI state is recomputed from it, which is why undo is free.
- `predict/model.py`: P(card | seen) over decks containing every seen card, with a fallback to similarity-weighted decks and a blend with the opponent's own history.
- `predict/crawler.py`, `db.py`: battle log crawl, the deck dataset and the player index in SQLite (`var/clasharness.db`).
- `vision/`: adb screenshot → crop → OpenRouter vision model returning JSON `{player_name, clan_name}` → tag resolver.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest
```

The tests cover elixir, cycle, prediction, the resolver, the vision reader (with a mocked client) and a full scripted match through the HTTP API against a fake Clash Royale server.

Elixir rules and champion ability costs live in config and `src/clasharness/data/card_overrides.json`. Update them if Supercell rebalances. `cards_fallback.json` is only used until the API card list has been fetched.
