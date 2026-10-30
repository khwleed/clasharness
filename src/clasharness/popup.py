"""Small always-on-top search box for logging an opponent card we haven't seen yet.

Runs as its own process (Tk wants the main thread, which uvicorn already owns):
    python -m clasharness.popup --port 8765
Type part of a name, arrows to pick, Enter to log, Esc to cancel.
"""

from __future__ import annotations

import argparse

import httpx
from rapidfuzz import fuzz, process, utils


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    import tkinter as tk

    cards = httpx.get(f"{base}/api/cards").json()
    state = httpx.get(f"{base}/api/state").json()
    seen = set(state["match"].get("opp", {}).get("revealed", [])) if state["match"].get("opp") else set()
    predicted = [p["card"] for p in state["prediction"]["cards"]]
    names = {c["key"]: c["name"] for c in cards if c["key"] not in seen}
    default_order = [k for k in predicted if k in names] + sorted((k for k in names if k not in predicted), key=names.get)

    root = tk.Tk()
    root.title("Opponent played…")
    root.attributes("-topmost", True)
    root.geometry("320x300+200+200")
    entry = tk.Entry(root, font=("TkDefaultFont", 16))
    entry.pack(fill="x", padx=6, pady=6)
    listbox = tk.Listbox(root, font=("TkDefaultFont", 13), activestyle="dotbox")
    listbox.pack(fill="both", expand=True, padx=6, pady=(0, 6))
    shown: list[str] = []

    def refresh(*_):
        q = entry.get().strip()
        if q:
            hits = process.extract(q, names, scorer=fuzz.WRatio, processor=utils.default_process, limit=12)
            keys = [k for _, _, k in hits]
        else:
            keys = default_order[:12]
        shown[:] = keys
        listbox.delete(0, "end")
        for k in keys:
            listbox.insert("end", names[k])
        if keys:
            listbox.selection_set(0)

    def move(delta):
        sel = listbox.curselection()
        i = max(0, min(len(shown) - 1, (sel[0] if sel else 0) + delta))
        listbox.selection_clear(0, "end")
        listbox.selection_set(i)
        listbox.see(i)
        return "break"

    def submit(*_):
        sel = listbox.curselection()
        if shown:
            card = shown[sel[0] if sel else 0]
            httpx.post(f"{base}/api/command", json={"cmd": "play", "side": "opp", "card": card})
        root.destroy()

    entry.bind("<KeyRelease>", lambda e: None if e.keysym in ("Up", "Down", "Return", "Escape") else refresh())
    entry.bind("<Down>", lambda e: move(1))
    entry.bind("<Up>", lambda e: move(-1))
    root.bind("<Return>", submit)
    root.bind("<Escape>", lambda e: root.destroy())
    listbox.bind("<Double-Button-1>", submit)
    refresh()
    root.after(50, lambda: (root.focus_force(), entry.focus_set()))
    root.mainloop()


if __name__ == "__main__":
    main()
