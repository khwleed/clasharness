"""Stream the phone screen into the app and send taps back, with no scrcpy window.

This talks to scrcpy's own on-device server (the `scrcpy-server` file that ships with
scrcpy) the way the scrcpy app does:

1. push the server to the phone and start it with `adb shell app_process`,
2. `adb forward` a local port to its socket and open two connections to it:
   the first carries the H.264 video, the second carries control messages,
3. pass the video packets on to the browser (WebCodecs decodes them there), and turn
   pointer events from the browser into scrcpy touch messages.

It keeps the packets since the last keyframe so a viewer that connects mid-stream can
start decoding straight away, and it reconnects on its own when the phone is unplugged
and plugged back in.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import shutil
import struct
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)

REMOTE_PATH = "/data/local/tmp/clasharness-scrcpy-server.jar"
DEVICE_NAME_LENGTH = 64
# The first packet is always the codec config. Older servers flag it with bit 63 (and
# keyframes with bit 62); newer ones moved the flags down a bit to make room for a
# session flag. We tell the two apart by which bit the first packet sets.
OLD_FLAGS = (1 << 63, 1 << 62)
NEW_FLAGS = (1 << 62, 1 << 61)
GOP_CACHE_LIMIT = 24 * 1024 * 1024

# Packet kinds sent to the browser as the first byte of each binary message.
KIND_CONFIG, KIND_KEY, KIND_DELTA = 0, 1, 2

# scrcpy control messages.
MSG_INJECT_TOUCH = 2
MSG_BACK_OR_SCREEN_ON = 4
POINTER_ID_FINGER = (1 << 64) - 2  # SC_POINTER_ID_GENERIC_FINGER (-2 as u64)
ACTION_DOWN, ACTION_UP, ACTION_MOVE = 0, 1, 2


class PhoneError(Exception):
    pass


def touch_message(action: int, x: int, y: int, width: int, height: int) -> bytes:
    """scrcpy INJECT_TOUCH_EVENT: 32 bytes, big-endian."""
    if action not in (ACTION_DOWN, ACTION_UP, ACTION_MOVE):
        raise ValueError(f"bad touch action {action}")
    pressure = 0 if action == ACTION_UP else 0xFFFF
    return struct.pack(">BBQiiHHHII", MSG_INJECT_TOUCH, action, POINTER_ID_FINGER,
                       x, y, width, height, pressure, 0, 0)


def back_messages() -> list[bytes]:
    """Back button (or wake the screen if it's off): a press and a release."""
    return [struct.pack(">BB", MSG_BACK_OR_SCREEN_ON, a) for a in (ACTION_DOWN, ACTION_UP)]


def find_server(scrcpy_path: str, configured: str = "") -> Path | None:
    """Locate scrcpy-server: the config, $SCRCPY_SERVER_PATH, then next to scrcpy."""
    candidates: list[Path] = []
    for p in (configured, os.environ.get("SCRCPY_SERVER_PATH", "")):
        if p:
            candidates.append(Path(p).expanduser())
    exe = shutil.which(scrcpy_path)
    if exe:
        for b in dict.fromkeys([Path(exe), Path(exe).resolve()]):
            candidates += [b.parent.parent / "share" / "scrcpy" / "scrcpy-server", b.parent / "scrcpy-server"]
    candidates += [Path(p) / "share/scrcpy/scrcpy-server" for p in ("/opt/homebrew", "/usr/local", "/usr")]
    return next((c for c in candidates if c.is_file()), None)


def parse_version(output: str) -> str | None:
    """'scrcpy 3.3.1 <https://…>' -> '3.3.1'."""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "scrcpy":
            return parts[1]
    return None


class PhoneMirror:
    def __init__(self, cfg: dict, scrcpy_path: str = "scrcpy", adb_path: str = "adb"):
        self.cfg = cfg
        self.scrcpy_path = scrcpy_path
        self.adb_path = adb_path
        self.state = "stopped"
        self.detail = ""
        self.device = ""
        self.log: deque[str] = deque(maxlen=30)
        self._viewers: set[asyncio.Queue] = set()
        self._gop: list[bytes] = []  # config + keyframe + deltas since, ready to replay
        self._gop_bytes = 0
        self._control: asyncio.StreamWriter | None = None
        self._video: asyncio.StreamWriter | None = None  # kept so the video socket stays open
        self._proc: asyncio.subprocess.Process | None = None
        self._forward_port: int | None = None
        self._task: asyncio.Task | None = None
        self.on_change = None  # called when the status changes

    # ---- status --------------------------------------------------------
    def status(self) -> dict:
        return {"state": self.state, "detail": self.detail, "device": self.device, "log": list(self.log)[-5:]}

    def _set(self, state: str, detail: str = "") -> None:
        if (state, detail) == (self.state, self.detail):
            return
        self.state, self.detail = state, detail
        if detail:
            log.info("phone %s: %s", state, detail)
        self._broadcast({"type": "status", **self.status()})
        if self.on_change:
            self.on_change()

    # ---- viewers -------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=400)
        q.put_nowait({"type": "status", **self.status()})
        for packet in self._gop:
            q.put_nowait(packet)
        self._viewers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._viewers.discard(q)

    def _broadcast(self, item) -> None:
        for q in list(self._viewers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # The viewer fell behind: drop what it has queued and resync it from the
                # last keyframe instead of decoding stale video.
                while not q.empty():
                    q.get_nowait()
                for packet in self._gop:
                    q.put_nowait(packet)

    def _on_packet(self, kind: int, payload: bytes) -> None:
        packet = bytes([kind]) + payload
        if kind == KIND_CONFIG:
            self._gop, self._gop_bytes = [packet], len(packet)
        elif kind == KIND_KEY:
            self._gop = [*self._gop[:1], packet]
            self._gop_bytes = sum(map(len, self._gop))
        elif len(self._gop) >= 2 and self._gop_bytes < GOP_CACHE_LIMIT:
            self._gop.append(packet)
            self._gop_bytes += len(packet)
        else:
            # No keyframe yet, or the cache is full: new viewers wait for the next one.
            del self._gop[1:]
        self._broadcast(packet)

    # ---- input ---------------------------------------------------------
    def handle_input(self, msg: dict) -> None:
        """Pointer input from the browser, in video-frame pixels."""
        if self._control is None or self._control.is_closing():
            return
        kind = msg.get("t")
        if kind == "touch":
            data = [touch_message(int(msg["a"]), int(msg["x"]), int(msg["y"]), int(msg["w"]), int(msg["h"]))]
        elif kind == "back":
            data = back_messages()
        else:
            return
        for d in data:
            self._control.write(d)

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self._cleanup()
        self._set("stopped")

    async def _run_forever(self) -> None:
        while True:
            try:
                await self._session()
                self._set("waiting", "Phone stream ended; reconnecting…")
            except PhoneError as e:
                self._set("error" if "not found" in str(e) or "version" in str(e) else "waiting", str(e))
            except (ConnectionError, asyncio.IncompleteReadError, OSError) as e:
                self._set("waiting", f"Lost the phone ({e.__class__.__name__}); reconnecting…")
            finally:
                await self._cleanup()
            await asyncio.sleep(2 if self.state == "waiting" else 10)

    async def _adb(self, *args: str, timeout: float = 20) -> str:
        serial = self.cfg.get("serial") or ""
        cmd = [self.adb_path, *(["-s", serial] if serial else []), *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except FileNotFoundError as e:
            raise PhoneError(f"{self.adb_path!r} not found. Install it with: brew install android-platform-tools") from e
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise PhoneError(f"adb {args[0]} timed out") from e
        text = out.decode(errors="replace").strip()
        if proc.returncode != 0:
            raise PhoneError(text or f"adb {args[0]} failed")
        return text

    async def _scrcpy_version(self) -> str:
        if v := self.cfg.get("server_version"):
            return v
        try:
            proc = await asyncio.create_subprocess_exec(
                self.scrcpy_path, "--version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), 10)
        except (FileNotFoundError, asyncio.TimeoutError) as e:
            raise PhoneError("scrcpy not found. Install it with: brew install scrcpy") from e
        version = parse_version(out.decode(errors="replace"))
        if not version:
            raise PhoneError("Couldn't read the scrcpy version from 'scrcpy --version'")
        return version

    async def _session(self) -> None:
        server = find_server(self.scrcpy_path, self.cfg.get("server_path") or "")
        if server is None:
            raise PhoneError("scrcpy-server not found. Install scrcpy (brew install scrcpy) "
                             "or set [phone].server_path in config.toml")
        version = await self._scrcpy_version()

        try:
            state = await self._adb("get-state", timeout=8)
        except PhoneError as e:
            text = str(e)
            if "more than one" in text:
                raise PhoneError("More than one phone/emulator is connected; set [phone].serial in config.toml") from e
            if "unauthorized" in text:
                raise PhoneError("Accept the USB debugging prompt on your phone") from e
            raise PhoneError("Waiting for your phone: plug it in, unlock it and allow USB debugging") from e
        if state != "device":
            raise PhoneError(f"Phone is {state}; unlock it and allow USB debugging")

        self._set("starting", "Starting the phone stream…")
        await self._adb("push", str(server), REMOTE_PATH, timeout=60)
        scid = random.randrange(1, 0x7FFFFFFF)
        port = int(await self._adb("forward", "tcp:0", f"localabstract:scrcpy_{scid:08x}"))
        self._forward_port = port

        opts = {
            "scid": f"{scid:08x}",
            "log_level": "info",
            "audio": "false",
            "control": "true",
            "video_codec": "h264",
            "max_size": int(self.cfg.get("max_size", 1600)),
            "video_bit_rate": int(self.cfg.get("bit_rate", 8_000_000)),
            "max_fps": int(self.cfg.get("max_fps", 60)),
            # A keyframe every second, so a viewer can join or recover quickly.
            "video_codec_options": f"i-frame-interval:int={int(self.cfg.get('keyframe_interval', 1))}",
            "tunnel_forward": "true",
            "stay_awake": str(bool(self.cfg.get("stay_awake", True))).lower(),
            "clipboard_autosync": "false",
            "send_device_meta": "true",
            "send_frame_meta": "true",
            "send_dummy_byte": "true",
            # The option is send_codec_meta on older servers and send_stream_meta on newer
            # ones. Each ignores the other with a warning.
            "send_codec_meta": "false",
            "send_stream_meta": "false",
        }
        serial = self.cfg.get("serial") or ""
        self._proc = await asyncio.create_subprocess_exec(
            self.adb_path, *(["-s", serial] if serial else []), "shell",
            f"CLASSPATH={REMOTE_PATH}", "app_process", "/", "com.genymobile.scrcpy.Server", version,
            *(f"{k}={v}" for k, v in opts.items()),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        pump = asyncio.create_task(self._pump_log(self._proc))

        try:
            video, self._video = await self._connect_video(port)
            ctrl_reader, self._control = await asyncio.open_connection("127.0.0.1", port)
            name = await video.readexactly(DEVICE_NAME_LENGTH)
            self.device = name.split(b"\0", 1)[0].decode(errors="replace")
            drain = asyncio.create_task(self._drain(ctrl_reader))
            try:
                await self._read_video(video)
            finally:
                drain.cancel()
        finally:
            pump.cancel()

    async def _connect_video(self, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # adb accepts the connection even before the server listens, then drops it, so
        # retry until the server's dummy byte arrives.
        for _ in range(100):
            if self._proc and self._proc.returncode is not None:
                break
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            except (ConnectionError, OSError):
                await asyncio.sleep(0.1)
                continue
            try:
                await asyncio.wait_for(reader.readexactly(1), 2)
                return reader, writer
            except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
                writer.close()
                await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)  # let the log pump catch the server's last words
        last = next((ln for ln in reversed(self.log) if "ERROR" in ln or "Exception" in ln), "")
        if "does not match the client" in last:
            raise PhoneError(f"scrcpy version mismatch: {last}")
        raise PhoneError(f"The phone stream didn't start{': ' + last if last else ''}")

    async def _read_video(self, video: asyncio.StreamReader) -> None:
        config_flag = key_flag = 0
        self._set("streaming", f"Streaming {self.device}")
        while True:
            header = await video.readexactly(12)
            pts_flags, size = struct.unpack(">QI", header)
            payload = await video.readexactly(size)
            if not config_flag:
                config_flag, key_flag = OLD_FLAGS if pts_flags & OLD_FLAGS[0] else NEW_FLAGS
            if pts_flags & config_flag:
                kind = KIND_CONFIG
            elif pts_flags & key_flag:
                kind = KIND_KEY
            else:
                kind = KIND_DELTA
            self._on_packet(kind, payload)

    @staticmethod
    async def _drain(reader: asyncio.StreamReader) -> None:
        # Device messages (clipboard etc.) aren't used; read them so the socket never fills.
        with contextlib.suppress(Exception):
            while await reader.read(4096):
                pass

    async def _pump_log(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        async for line in proc.stdout:
            text = line.decode(errors="replace").rstrip()
            if text:
                self.log.append(text)
                log.debug("scrcpy-server: %s", text)

    async def _cleanup(self) -> None:
        for w in (self._control, self._video):
            if w:
                w.close()
        self._control = self._video = None
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._proc.wait(), 3)
        self._proc = None
        if self._forward_port:
            with contextlib.suppress(Exception):
                await self._adb("forward", "--remove", f"tcp:{self._forward_port}", timeout=5)
            self._forward_port = None
        self._gop, self._gop_bytes = [], 0
