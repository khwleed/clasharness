"""Launch and watch scrcpy, which mirrors and controls the Android phone."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import deque


class Scrcpy:
    def __init__(self, path: str = "scrcpy", args: list[str] | None = None):
        self.path = path
        self.args = args or []
        self.proc: subprocess.Popen | None = None
        self.output: deque[str] = deque(maxlen=20)

    @property
    def available(self) -> bool:
        return shutil.which(self.path) is not None

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        if not self.available:
            self.output.append(f"{self.path!r} not found on PATH; install scrcpy (brew/apt install scrcpy)")
            return
        self.output.clear()
        self.proc = subprocess.Popen(
            [self.path, *self.args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.output.append(line.rstrip())

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def status(self) -> dict:
        if not self.proc:
            state = "stopped"
        elif self.proc.poll() is None:
            state = "running"
        else:
            state = f"exited ({self.proc.returncode})"
        return {"state": state, "available": self.available, "log": list(self.output)[-5:]}
