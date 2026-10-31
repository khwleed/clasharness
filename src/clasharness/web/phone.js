"use strict";
// Embedded phone screen: decodes the H.264 stream from /ws/phone with WebCodecs and
// sends mouse/trackpad input back as touches. Right-click (or the Back button) is Back.

(() => {
  const box = document.getElementById("phone");
  const layout = document.getElementById("layout");
  const canvas = document.getElementById("phone-canvas");
  const msg = document.getElementById("phone-msg");
  const deviceLabel = document.getElementById("phone-device");
  const ctx = canvas.getContext("2d");

  const KIND_CONFIG = 0, KIND_KEY = 1;
  const TOUCH_DOWN = 0, TOUCH_UP = 1, TOUCH_MOVE = 2;

  let ws = null;
  let decoder = null;
  let config = null;      // SPS/PPS (Annex B), prepended to every keyframe
  let needKey = true;
  let timestamp = 0;
  let vw = 0, vh = 0;     // video size = the phone's screen size as scrcpy sees it
  let streaming = false;
  let disabled = false;
  let touching = false;

  const setMsg = (text) => { msg.textContent = text || ""; };

  function show() {
    box.hidden = false;
    layout.classList.add("with-phone");
  }

  // "avc1.PPCCLL" from the SPS NAL unit (profile, constraints, level).
  function codecString(data) {
    for (let i = 0; i + 6 < data.length; i++) {
      if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1 && (data[i + 3] & 0x1f) === 7) {
        const hex = (b) => b.toString(16).padStart(2, "0");
        return `avc1.${hex(data[i + 4])}${hex(data[i + 5])}${hex(data[i + 6])}`;
      }
    }
    return "avc1.42e01f";
  }

  function closeDecoder() {
    if (decoder && decoder.state !== "closed") decoder.close();
    decoder = null;
    needKey = true;
  }

  function openDecoder() {
    closeDecoder();
    decoder = new VideoDecoder({
      output: draw,
      error: (e) => { console.warn("phone decoder:", e); closeDecoder(); },
    });
    // No `description`, so the decoder expects Annex B with SPS/PPS in the keyframes.
    decoder.configure({ codec: codecString(config), optimizeForLatency: true });
  }

  function draw(frame) {
    if (frame.displayWidth !== vw || frame.displayHeight !== vh) {
      vw = canvas.width = frame.displayWidth;
      vh = canvas.height = frame.displayHeight;
      box.style.setProperty("--phone-ar", `${vw} / ${vh}`);
    }
    ctx.drawImage(frame, 0, 0);
    frame.close();
    if (streaming && msg.textContent) setMsg("");
  }

  function decode(type, data) {
    if (decoder.decodeQueueSize > 30) {
      // Decoding fell behind: skip ahead to the next keyframe (one comes every second).
      closeDecoder();
      return;
    }
    timestamp += 16667;
    decoder.decode(new EncodedVideoChunk({ type, timestamp, data }));
  }

  function onPacket(buffer) {
    const bytes = new Uint8Array(buffer);
    const kind = bytes[0];
    const data = bytes.subarray(1);
    if (kind === KIND_CONFIG) {
      config = data.slice();
      closeDecoder();
      return;
    }
    if (!config) return;
    if (kind === KIND_KEY) {
      if (!decoder) openDecoder();
      const merged = new Uint8Array(config.length + data.length);
      merged.set(config);
      merged.set(data, config.length);
      decode("key", merged);
      needKey = false;
    } else if (decoder && !needKey) {
      decode("delta", data);
    }
  }

  function onStatus(s) {
    if (s.state === "disabled") {
      disabled = true;
      box.hidden = true;
      layout.classList.remove("with-phone");
      return;
    }
    show();
    deviceLabel.textContent = s.device || "";
    streaming = s.state === "streaming";
    if (!streaming) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      setMsg(s.detail || "Connecting to your phone…");
    } else if (!vw) {
      setMsg("Waiting for video…");
    }
  }

  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/phone`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (e) => {
      if (typeof e.data === "string") onStatus(JSON.parse(e.data));
      else onPacket(e.data);
    };
    ws.onclose = () => {
      closeDecoder();
      config = null;
      streaming = false;
      if (!disabled) {
        setMsg("Reconnecting to the app…");
        setTimeout(connect, 1500);
      }
    };
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  // Pointer position -> video pixels, allowing for object-fit: contain letterboxing.
  function toVideo(e) {
    const r = canvas.getBoundingClientRect();
    const scale = Math.min(r.width / vw, r.height / vh);
    const left = r.left + (r.width - vw * scale) / 2;
    const top = r.top + (r.height - vh * scale) / 2;
    const x = Math.round((e.clientX - left) / scale);
    const y = Math.round((e.clientY - top) / scale);
    return { x: Math.max(0, Math.min(vw - 1, x)), y: Math.max(0, Math.min(vh - 1, y)) };
  }

  function touch(action, e) {
    const p = toVideo(e);
    send({ t: "touch", a: action, x: p.x, y: p.y, w: vw, h: vh });
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (!e.isPrimary || e.button !== 0 || !streaming || !vw) return;
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    touching = true;
    touch(TOUCH_DOWN, e);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (touching && e.isPrimary) touch(TOUCH_MOVE, e);
  });
  const release = (e) => {
    if (!touching || !e.isPrimary) return;
    touching = false;
    touch(TOUCH_UP, e);
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    send({ t: "back" });
  });
  document.getElementById("phone-back").addEventListener("click", () => send({ t: "back" }));

  if (!("VideoDecoder" in window)) {
    show();
    setMsg("This window can't decode video (no WebCodecs). Update macOS, or open this page in Chrome.");
  } else {
    connect();
  }
})();
