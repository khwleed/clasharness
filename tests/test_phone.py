"""Embedded phone stream, run end to end against a fake adb and a fake scrcpy server."""

import asyncio
import json
import stat
import struct
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from clasharness import phone as phone_mod
from clasharness.config import DEFAULTS, Config, _merge
from clasharness.harness import Harness
from clasharness.phone import (KIND_CONFIG, KIND_DELTA, KIND_KEY, NEW_FLAGS, OLD_FLAGS, PhoneMirror,
                               back_messages, find_server, parse_version, touch_message)
from clasharness.server import create_app

CONFIG = b"\x00\x00\x00\x01\x67\x42\xc0\x1f" + b"\x00\x00\x00\x01\x68\xce"
KEY = b"\x00\x00\x00\x01\x65" + b"K" * 50
DELTA = b"\x00\x00\x00\x01\x41" + b"D" * 20


def test_touch_message_matches_scrcpy_layout():
    m = touch_message(0, 100, 200, 1080, 2400)
    assert len(m) == 32
    typ, action, pid, x, y, w, h, pressure, action_button, buttons = struct.unpack(">BBQiiHHHII", m)
    assert (typ, action, x, y, w, h) == (2, 0, 100, 200, 1080, 2400)
    assert pid == 2**64 - 2  # generic finger
    assert pressure == 0xFFFF and action_button == buttons == 0
    assert struct.unpack(">BBQiiHHHII", touch_message(1, 1, 2, 3, 4))[7] == 0  # released
    assert back_messages() == [b"\x04\x00", b"\x04\x01"]
    with pytest.raises(ValueError):
        touch_message(7, 0, 0, 1, 1)


def test_parse_version():
    assert parse_version("scrcpy 3.3.1 <https://github.com/Genymobile/scrcpy>\n\nDependencies…") == "3.3.1"
    assert parse_version("nonsense") is None


def test_find_server(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRCPY_SERVER_PATH", raising=False)
    (tmp_path / "bin").mkdir()
    exe = tmp_path / "bin" / "scrcpy"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    jar = tmp_path / "share" / "scrcpy" / "scrcpy-server"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"jar")
    assert find_server(str(exe)) == jar
    other = tmp_path / "custom-server"
    other.write_bytes(b"jar")
    assert find_server(str(exe), str(other)) == other


class FakeScrcpyServer:
    """Speaks the device side of the scrcpy protocol on a local TCP port."""

    def __init__(self, flags):
        self.config_flag, self.key_flag = flags
        self.control = bytearray()
        self.connections = 0
        self.got_control = asyncio.Event()

    async def handle(self, reader, writer):
        self.connections += 1
        if self.connections == 1:  # video socket
            writer.write(b"\x00" + b"Galaxy S24".ljust(64, b"\x00"))
            for flags, payload in [(self.config_flag, CONFIG), (self.key_flag | 1000, KEY), (2000, DELTA), (3000, DELTA)]:
                writer.write(struct.pack(">QI", flags, len(payload)) + payload)
            await writer.drain()
            await reader.read()  # keep it open until the client goes away
        else:  # control socket
            while data := await reader.read(1024):
                self.control += data
                self.got_control.set()
        writer.close()


def fake_tools(tmp_path, port):
    """A fake adb that 'forwards' to `port` and a fake scrcpy reporting version 9.9."""
    adb = tmp_path / "adb"
    adb.write_text(f"""#!{sys.executable}
import sys, time
args = [a for a in sys.argv[1:]]
if args[:2] == ["-s", "SERIAL"]:
    args = args[2:]
cmd = args[0]
with open({str(tmp_path / 'adb.log')!r}, "a") as f:
    f.write(" ".join(args) + "\\n")
if cmd == "get-state":
    print("device")
elif cmd == "forward" and args[1] != "--remove":
    print({port})
elif cmd == "shell":
    print("[server] INFO: Device: fake")
    sys.stdout.flush()
    time.sleep(60)
""")
    scrcpy = tmp_path / "scrcpy"
    scrcpy.write_text("#!/bin/sh\necho 'scrcpy 9.9 <https://github.com/Genymobile/scrcpy>'\n")
    jar = tmp_path / "scrcpy-server"
    jar.write_bytes(b"jar")
    for f in (adb, scrcpy):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return adb, scrcpy, jar


async def collect(q, n, timeout=5):
    items = []
    while len(items) < n:
        items.append(await asyncio.wait_for(q.get(), timeout))
    return items


async def collect_packets(q, n, timeout=5):
    """The next n video packets, skipping status messages."""
    out = []
    while len(out) < n:
        item = await asyncio.wait_for(q.get(), timeout)
        if isinstance(item, bytes):
            out.append(item)
    return out


@pytest.mark.parametrize("flags", [OLD_FLAGS, NEW_FLAGS], ids=["old-server", "new-server"])
async def test_stream_and_touch(tmp_path, flags):
    fake = FakeScrcpyServer(flags)
    server = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    adb, scrcpy, jar = fake_tools(tmp_path, port)

    mirror = PhoneMirror({**DEFAULTS["phone"], "server_path": str(jar), "serial": "SERIAL"}, str(scrcpy), str(adb))
    viewer = mirror.subscribe()
    mirror.start()
    try:
        packets = await collect_packets(viewer, 4)
        assert [p[0] for p in packets] == [KIND_CONFIG, KIND_KEY, KIND_DELTA, KIND_DELTA]
        assert packets[0][1:] == CONFIG and packets[1][1:] == KEY and packets[2][1:] == DELTA
        assert mirror.state == "streaming" and mirror.device == "Galaxy S24"

        # A viewer joining mid-stream gets the status and everything since the keyframe.
        late = mirror.subscribe()
        replay = await collect(late, 5)
        assert replay[0]["state"] == "streaming"
        assert replay[1:] == packets

        mirror.handle_input({"t": "touch", "a": 0, "x": 10, "y": 20, "w": 1080, "h": 2400})
        mirror.handle_input({"t": "back"})
        await asyncio.wait_for(fake.got_control.wait(), 5)
        for _ in range(50):
            if len(fake.control) >= 36:
                break
            await asyncio.sleep(0.02)
        assert bytes(fake.control) == touch_message(0, 10, 20, 1080, 2400) + b"\x04\x00\x04\x01"
        for _ in range(150):  # the fake adb's shell may still be starting up
            if "shell" in (tmp_path / "adb.log").read_text():
                break
            await asyncio.sleep(0.02)
    finally:
        await mirror.stop()
        server.close()

    log = (tmp_path / "adb.log").read_text()
    assert "push" in log and f"forward --remove tcp:{port}" in log
    shell = next(line for line in log.splitlines() if line.startswith("shell"))
    assert "com.genymobile.scrcpy.Server 9.9" in shell and "tunnel_forward=true" in shell
    assert "video_codec=h264" in shell and "audio=false" in shell


async def test_waits_for_phone(tmp_path):
    adb = tmp_path / "adb"
    adb.write_text("#!/bin/sh\necho 'error: no devices/emulators found'\nexit 1\n")
    adb.chmod(0o755)
    jar = tmp_path / "scrcpy-server"
    jar.write_bytes(b"jar")
    mirror = PhoneMirror({**DEFAULTS["phone"], "server_path": str(jar), "server_version": "9.9"}, "scrcpy", str(adb))
    mirror.start()
    try:
        for _ in range(100):
            if mirror.state == "waiting":
                break
            await asyncio.sleep(0.02)
        assert mirror.state == "waiting" and "plug it in" in mirror.detail
    finally:
        await mirror.stop()


def test_ws_when_disabled(tmp_path, api):
    cfg = Config(_merge(DEFAULTS, {"storage": {"dir": str(tmp_path)}, "vision": {"enabled": False},
                                   "phone": {"embed": False}, "predict": {"trickle_players": 0}}))
    with TestClient(create_app(Harness(cfg, api=api))) as client:
        with client.websocket_connect("/ws/phone") as ws:
            assert ws.receive_json()["state"] == "disabled"
        assert client.get("/api/state").json()["phone"] is None


def test_ws_streams_and_forwards_taps(tmp_path, api):
    """The browser's path: /ws/phone gets video and its taps reach the phone."""
    received = bytearray()
    loop = asyncio.new_event_loop()
    fake = FakeScrcpyServer(NEW_FLAGS)
    server = loop.run_until_complete(asyncio.start_server(fake.handle, "127.0.0.1", 0))
    port = server.sockets[0].getsockname()[1]
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    adb, scrcpy, jar = fake_tools(tmp_path, port)

    cfg = Config(_merge(DEFAULTS, {
        "storage": {"dir": str(tmp_path)}, "vision": {"enabled": False, "adb_path": str(adb)},
        "scrcpy": {"path": str(scrcpy)}, "phone": {"server_path": str(jar)}, "predict": {"trickle_players": 0},
    }))
    try:
        with TestClient(create_app(Harness(cfg, api=api))) as client:
            with client.websocket_connect("/ws/phone") as ws:
                kinds = []
                while len(kinds) < 4:
                    msg = ws.receive()
                    if msg.get("bytes"):
                        kinds.append(msg["bytes"][0])
                assert kinds == [KIND_CONFIG, KIND_KEY, KIND_DELTA, KIND_DELTA]
                ws.send_text(json.dumps({"t": "touch", "a": 0, "x": 5, "y": 6, "w": 1080, "h": 2400}))
                ws.send_text(json.dumps({"t": "touch", "a": "junk"}))  # ignored, not fatal
                ws.send_text(json.dumps({"t": "touch", "a": 1, "x": 5, "y": 6, "w": 1080, "h": 2400}))
                end = time.time() + 5
                while len(fake.control) < 64 and time.time() < end:
                    time.sleep(0.02)
                received += fake.control
            assert client.get("/api/state").json()["phone"]["state"] == "streaming"
    finally:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
    assert bytes(received) == touch_message(0, 5, 6, 1080, 2400) + touch_message(1, 5, 6, 1080, 2400)


def test_gop_cache_bounds():
    m = PhoneMirror(DEFAULTS["phone"])
    m._on_packet(KIND_DELTA, b"x")  # before any config: nothing cached
    assert m._gop == []
    m._on_packet(KIND_CONFIG, b"c")
    m._on_packet(KIND_DELTA, b"d")  # no keyframe yet: not cached
    assert m._gop == [b"\x00c"]
    m._on_packet(KIND_KEY, b"k")
    m._on_packet(KIND_DELTA, b"d")
    assert m._gop == [b"\x00c", b"\x01k", b"\x02d"]
    m._on_packet(KIND_KEY, b"k2")
    assert m._gop == [b"\x00c", b"\x01k2"]
    m._gop_bytes = phone_mod.GOP_CACHE_LIMIT
    m._on_packet(KIND_DELTA, b"d")
    assert m._gop == [b"\x00c"]
