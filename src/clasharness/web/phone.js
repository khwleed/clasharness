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
      requestAnimationFrame(placeMarkers);
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
    if (picking === "spot" && e.button === 0) {
      e.preventDefault();
      pickSpot(e);
      return;
    }
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

  // ---------- tap keys: plain keys that tap set spots, while this window has focus ----------
  const editor = document.getElementById("tap-editor");
  const help = document.getElementById("tap-help");
  const list = document.getElementById("tap-list");
  const markers = document.getElementById("tap-markers");
  const toggle = document.getElementById("tap-toggle");
  const addBtn = document.getElementById("tap-add");
  const cancelBtn = document.getElementById("tap-cancel");
  const screenBox = document.getElementById("phone-screen");

  let taps = [];          // [{key, x, y, name}], x/y as fractions of the screen
  let tapsJson = "";
  let globalKeys = {};    // key -> what the app's own plain hotkeys use it for
  let picking = null;     // null | "spot" (click the screen) | "key" (press a key)
  let pending = null;     // {x, y} chosen in "spot"
  let held = null;        // tap key currently held down
  const HELP = "These keys tap their spot on the phone while this window has focus " +
               "(not while typing in a box). Hold a key to hold the tap.";

  const normKey = (e) => (e.key === " " ? "space" : e.key.toLowerCase());
  const keyText = (k) => (k === "space" ? "Space" : k === "backspace" ? "⌫" : k.length === 1 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1));
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const RESERVED = new Set(["escape", "tab", "enter", "meta", "control", "alt", "shift", "capslock", "dead", "unidentified"]);

  window.onPhoneState = (s) => {
    globalKeys = {};
    const hk = s.hotkeys || {};
    const plain = (k) => (typeof k === "string" && !k.includes("+") ? k.replace(/[<>]/g, "") : null);
    for (const k of hk.coach_opp || []) if (plain(k)) globalKeys[plain(k)] = "coach mode";
    for (const k of hk.coach_hand || []) if (plain(k)) globalKeys[plain(k)] = "coach mode";
    if (plain(hk.coach_undo)) globalKeys[plain(hk.coach_undo)] = "coach mode";
    const json = JSON.stringify(s.phone_taps || []);
    if (json !== tapsJson && !typing(document.activeElement)) {
      tapsJson = json;
      taps = s.phone_taps || [];
      renderTaps();
    }
  };

  async function saveTaps(next) {
    const r = await command({ cmd: "set_taps", taps: next });
    if (r) {
      taps = r.taps;
      tapsJson = JSON.stringify(taps);
      renderTaps();
    }
  }

  function renderTaps() {
    list.innerHTML = taps.length ? taps.map((t, i) => `<div class="tap-row">
        <kbd>${esc(keyText(t.key))}</kbd>
        <input data-tap-name="${i}" value="${esc(t.name)}" placeholder="Name (optional)">
        <button class="ghost" data-tap-del="${i}" title="Remove">×</button>
        ${globalKeys[t.key] ? `<div class="warn-note">Also a ${esc(globalKeys[t.key])} key: both happen when coach mode is on.</div>` : ""}
      </div>`).join("") : `<div class="muted small">No tap keys yet.</div>`;
    setPicking(picking);
    placeMarkers();
  }

  // Where the video sits inside the screen box (object-fit: contain).
  function videoBox() {
    const r = screenBox.getBoundingClientRect();
    if (!vw) return { left: 0, top: 0, w: r.width, h: r.height };
    const scale = Math.min(r.width / vw, r.height / vh);
    return { left: (r.width - vw * scale) / 2, top: (r.height - vh * scale) / 2, w: vw * scale, h: vh * scale };
  }

  function placeMarkers() {
    const b = videoBox();
    const dot = (t, cls) => `<div class="tap-marker ${cls}" data-key="${esc(t.key || "")}"
      style="left:${b.left + t.x * b.w}px;top:${b.top + t.y * b.h}px">${esc(t.key ? keyText(t.key) : "?")}</div>`;
    markers.innerHTML = taps.map((t) => dot(t, "")).join("") + (pending ? dot(pending, "pending") : "");
  }
  window.addEventListener("resize", placeMarkers);

  function setPicking(mode) {
    picking = mode;
    box.classList.toggle("picking", mode === "spot");
    help.classList.toggle("active", !!mode);
    help.textContent = mode === "spot" ? "Click the spot on the phone screen this key should tap."
                     : mode === "key" ? "Now press the key for it (Esc to cancel). Pressing a key that's already set moves it here."
                     : HELP;
    addBtn.hidden = !!mode;
    cancelBtn.hidden = !mode;
  }

  function cancelPick() {
    pending = null;
    setPicking(null);
    placeMarkers();
  }

  function pickSpot(e) {
    const r = canvas.getBoundingClientRect();
    const b = videoBox();
    const fx = (e.clientX - r.left - b.left) / b.w;
    const fy = (e.clientY - r.top - b.top) / b.h;
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return;
    pending = { x: fx, y: fy };
    setPicking("key");
    placeMarkers();
  }

  function setEditing(on) {
    editor.hidden = !on;
    box.classList.toggle("editing", on);
    toggle.setAttribute("aria-pressed", String(on));
    if (!on) cancelPick();
    requestAnimationFrame(placeMarkers);
  }

  toggle.addEventListener("click", () => setEditing(editor.hidden));
  addBtn.addEventListener("click", () => setPicking("spot"));
  cancelBtn.addEventListener("click", cancelPick);
  list.addEventListener("click", (e) => {
    const i = e.target.dataset?.tapDel;
    if (i !== undefined) saveTaps(taps.filter((_, j) => j !== Number(i)));
  });
  list.addEventListener("change", (e) => {
    const i = e.target.dataset?.tapName;
    if (i !== undefined) saveTaps(taps.map((t, j) => (j === Number(i) ? { ...t, name: e.target.value } : t)));
  });

  function flash(key) {
    const m = markers.querySelector(`.tap-marker[data-key="${CSS.escape(key)}"]`);
    if (!m) return;
    m.classList.add("hit");
    setTimeout(() => m.classList.remove("hit"), 150);
  }

  function tapTouch(action, t) {
    const x = Math.round(Math.min(1, Math.max(0, t.x)) * (vw - 1));
    const y = Math.round(Math.min(1, Math.max(0, t.y)) * (vh - 1));
    send({ t: "touch", a: action, x, y, w: vw, h: vh });
  }

  function releaseHeld() {
    if (!held) return;
    tapTouch(TOUCH_UP, held);
    held = null;
  }

  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;  // leave modifier combos to the app's hotkeys
    const key = normKey(e);
    if (picking) {
      if (key === "escape") { cancelPick(); e.preventDefault(); return; }
      if (picking !== "key" || RESERVED.has(key) || typing(e.target)) return;
      e.preventDefault();
      const rest = taps.filter((t) => t.key !== key);
      const old = taps.find((t) => t.key === key);
      saveTaps([...rest, { key, x: pending.x, y: pending.y, name: old?.name || "" }]);
      pending = null;
      setPicking(null);
      return;
    }
    if (typing(e.target)) return;
    const t = taps.find((tap) => tap.key === key);
    if (!t) return;
    e.preventDefault();
    if (e.repeat || touching || !streaming || !vw) return;
    releaseHeld();  // one finger: a second key lifts the first
    held = t;
    tapTouch(TOUCH_DOWN, t);
    flash(t.key);
  });
  document.addEventListener("keyup", (e) => {
    if (held && normKey(e) === held.key) releaseHeld();
  });
  window.addEventListener("blur", releaseHeld);
  setPicking(null);

  if (!("VideoDecoder" in window)) {
    show();
    setMsg("This window can't decode video (no WebCodecs). Update macOS, or open this page in Chrome.");
  } else {
    connect();
  }
})();
