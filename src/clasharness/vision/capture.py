"""Grab a frame from the phone over adb and crop the opponent's name banner."""

from __future__ import annotations

import asyncio
import io

from PIL import Image


class CaptureError(Exception):
    pass


async def screenshot(adb_path: str = "adb", timeout: float = 8.0) -> bytes:
    """PNG bytes of the phone screen (`adb exec-out screencap -p`)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            adb_path, "exec-out", "screencap", "-p",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise CaptureError(f"{adb_path!r} not found; install Android platform-tools") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError as e:
        proc.kill()
        raise CaptureError("adb screencap timed out") from e
    if proc.returncode != 0 or not out.startswith(b"\x89PNG"):
        raise CaptureError(err.decode(errors="replace").strip() or "adb returned no image (is the phone connected?)")
    return out


def crop(png: bytes, box: list[float], upscale: int = 2) -> bytes:
    """Crop by fractional (x0, y0, x1, y1) so it works at any resolution."""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    x0, y0, x1, y1 = box
    region = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    if upscale > 1:
        region = region.resize((region.width * upscale, region.height * upscale), Image.LANCZOS)
    buf = io.BytesIO()
    region.save(buf, "PNG")
    return buf.getvalue()
