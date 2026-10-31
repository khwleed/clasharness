"use strict";

const $ = (id) => document.getElementById(id);
let cards = {};          // key -> card
let byName = {};         // lowercased name -> key
let S = null;            // latest state snapshot
let lastVersion = -1;
let lastMatchesFetch = 0;
let coach = false;       // coach mode (server-side): only the opponent's side, keys 1–8

// ---------- helpers ----------
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function keyLabel(k) {
  if (!k) return "";
  if (k === "<backspace>") return "⌫";
  return k.replace(/<([^>]+)>/g, (_, m) => /^f\d+$/.test(m) ? m.toUpperCase() : m[0].toUpperCase() + m.slice(1))
          .replace(/(^|\+)([a-z])$/, (_, p, c) => p + c.toUpperCase());
}
function cardName(key) { return cards[key]?.name ?? key; }
function fmtClock(sec) {
  const s = Math.max(0, Math.ceil(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.className = "toast"), 2600);
}
async function command(body) {
  const r = await fetch("/api/command", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    toast(e.detail || `Error ${r.status}`, true);
    return null;
  }
  return r.json();
}

function tile(key, opts = {}) {
  if (!key) return `<div class="tile unknown" title="Not seen yet">?</div>`;
  const c = cards[key] || { name: key };
  const cls = ["tile", opts.cls || ""].join(" ");
  const data = opts.cmd ? ` data-play='${esc(JSON.stringify(opts.cmd))}'` : "";
  return `<div class="${cls}"${data} title="${esc(c.name)}">
    ${c.icon ? `<img src="${esc(c.icon)}" alt="" loading="lazy">` : ""}
    ${c.elixir != null ? `<span class="cost">${c.elixir}</span>` : ""}
    ${opts.key ? `<span class="key">${esc(keyLabel(opts.key))}</span>` : ""}
    <span class="name">${esc(c.name)}</span>
  </div>`;
}

// ---------- rendering ----------
function renderElixir(el, e) {
  if (!e) { el.innerHTML = ""; return; }
  const v = e.value;
  let segs = "";
  for (let i = 0; i < 10; i++) {
    const fill = Math.max(0, Math.min(1, v - i));
    segs += `<div class="seg"><i style="width:${fill * 100}%"></i></div>`;
  }
  el.className = "elixir" + (v >= 9.99 ? " full" : "");
  el.innerHTML = `<div class="num">${v.toFixed(1)}</div><div class="bar">${segs}</div>
    <div class="meta">leaked ${e.leaked.toFixed(1)}${e.deficits ? `<br><span style="color:var(--warn)">${e.deficits} overspend</span>` : ""}</div>`;
}

function renderHand(side, cyc, deck) {
  const hand = $(`hand-${side}`), note = $(`hand-note-${side}`);
  if (!cyc) { hand.innerHTML = ""; note.textContent = ""; return; }
  // Coach mode: once all 4 of the opponent's cards are known, Q/W/E/R play them in this order.
  const handKeys = side === "opp" && coach && cyc.hand.length === 4 && !cyc.hand_unknown ? S.hotkeys.coach_hand : [];
  const parts = cyc.hand.map((k, i) => tile(k, { key: handKeys[i], cmd: { cmd: "play", side, card: k } }));
  for (let i = 0; i < cyc.hand_unknown; i++) parts.push(tile(null));
  if (cyc.next) parts.push(`<div class="slot-next">${tile(cyc.next, { cls: "next" })}next</div>`);
  if (side === "opp") parts.push(undoKey());
  hand.innerHTML = parts.join("");
  if (cyc.hand_candidates.length) {
    note.textContent = side === "me"
      ? "· unknown until 4 cards are cycled"
      : `· could include ${cyc.hand_candidates.map(cardName).join(", ")}`;
  } else note.textContent = "";
}

// Undo sits next to the opponent's hand, where you're looking when you log the wrong card.
// It names what it would take back (the start can't be undone; an accidental End can).
function undoKey() {
  const ev = S.match.events || [];
  const last = ev.length > 1 ? ev[ev.length - 1] : null;
  const key = coach ? S.hotkeys.coach_undo : S.hotkeys.undo;
  return `<div class="undo-slot">
    <button class="undo-key" data-cmd="undo"${last ? "" : " disabled"} title="${esc(keyLabel(key))}">Undo <kbd class="keycap">${esc(keyLabel(key))}</kbd></button>
    <span class="undo-what">${last ? esc(eventText(last)) : "nothing to undo"}</span>
  </div>`;
}

function render() {
  const s = S;
  const m = s.match;
  const hk = s.hotkeys;
  const running = m.status === "running";

  coach = !!s.coach;
  document.body.classList.toggle("coach", coach);
  $("coach-toggle").setAttribute("aria-pressed", String(coach));
  renderKeyHints(hk);

  // clock + phase
  $("clock").textContent = m.status === "idle" ? "3:00" : fmtClock(m.clock_remaining);
  $("clock").parentElement.className = "clock" + (m.overtime ? " overtime" : "");
  const ph = $("phase");
  ph.textContent = m.status === "idle" ? "Idle" : m.status === "ended" ? "Ended" : `${m.phase.name} ×${m.phase.multiplier}`;
  ph.className = "phase" + (running && m.phase.multiplier >= 3 ? " x3" : running && m.phase.multiplier >= 2 ? " x2" : "");

  // chips
  const chip = (ok, text, title = "") => `<span class="chip ${ok ? "ok" : "bad"}" title="${esc(title)}">${esc(text)}</span>`;
  $("chips").innerHTML = [
    chip(s.api.enabled, s.api.enabled ? "API" : "API off", "Clash Royale API token"),
    s.phone ? chip(s.phone.state === "streaming", `phone ${s.phone.state}`, s.phone.detail)
            : chip(s.scrcpy.state === "running", `scrcpy ${s.scrcpy.state}`),
    chip(s.dataset.decks_loaded > 0, `${s.dataset.decks_loaded} decks`, "decks in prediction dataset"),
  ].join("");

  // sides
  for (const side of ["me", "opp"]) {
    const d = m[side];
    renderElixir($(`elixir-${side}`), d?.elixir);
    renderHand(side, d?.cycle);
    $(`warn-${side}`).textContent = d?.cycle?.warnings?.join(" · ") || "";
  }
  const notInHand = new Set(m.me?.cycle?.not_in_hand || []);
  $("deck-me").innerHTML = handRows(s.my_deck.map((k, i) => ({
    out: notInHand.has(k),
    html: tile(k, { key: hk.my_cards[i], cls: notInHand.has(k) ? "dim" : "", cmd: { cmd: "play", side: "me", slot: i } }),
  }))) || `<span class="muted">No deck yet: set your tag in Setup.</span>`;

  // Their known cards: seen ones first, then the rest of their assumed deck (dashed until played).
  const revealed = m.opp?.revealed || [];
  const oppCards = m.opp?.cards || revealed;
  const oppNot = new Set(m.opp?.cycle?.not_in_hand || []);
  const oppTiles = oppCards.map((k, i) => ({
    out: oppNot.has(k),
    html: tile(k, { key: coach ? hk.coach_opp[i] : hk.opp_seen[i],
                    cls: [oppNot.has(k) ? "dim" : "", revealed.includes(k) ? "" : "assumed"].join(" "),
                    cmd: { cmd: "play", side: "opp", slot: i, source: "seen" } }),
  }));
  // Unseen cards could be in hand, so they stay on the top row.
  for (let i = oppCards.length; i < 8; i++) oppTiles.push({ out: false, html: tile(null) });
  $("deck-opp").innerHTML = handRows(oppTiles);
  // Once their whole hand is known, the hand row is what matters: tone the grid down.
  const oppCyc = m.opp?.cycle;
  $("deck-opp").classList.toggle("secondary", !!oppCyc && oppCyc.hand.length === 4 && !oppCyc.hand_unknown);
  renderOppDeck(m);

  $("opp-name").textContent = [s.opponent.name, s.opponent.tag].filter(Boolean).join(" · ");
  $("my-name").textContent = [s.my_tag, s.my_trophies && `${s.my_trophies} 🏆`].filter(Boolean).join(" · ");

  renderPrediction(s);
  renderVision(s);
  renderTimeline(m);
  renderSetup(s);
}

// Cards that can be in hand on the top row, cards that can't (faded) on the row below.
// Each tile keeps its own key label, so the order on screen doesn't change the keys.
function handRows(items) {
  const row = (xs) => xs.length ? `<div class="tile-row">${xs.map((x) => x.html).join("")}</div>` : "";
  return row(items.filter((x) => !x.out)) + row(items.filter((x) => x.out));
}

// Key labels come from the active bindings, which differ by platform and mode.
function renderKeyHints(hk) {
  const range = (keys) => keys.length ? `<kbd>${esc(keyLabel(keys[0]))}</kbd>–<kbd>${esc(keyLabel(keys[keys.length - 1]))}</kbd>` : "";
  $("hint-opp-seen").innerHTML = range(coach ? hk.coach_opp : hk.opp_seen);
  $("hint-my-cards").innerHTML = range(hk.my_cards);
  $("opp-search").placeholder = `Log an unseen opponent card… (${keyLabel(hk.opp_search)})`;
  document.querySelectorAll("[data-hk]").forEach((el) => {
    const name = coach && el.dataset.hk === "undo" ? "coach_undo" : el.dataset.hk;  // Backspace in coach mode
    const label = keyLabel(hk[name]);
    el.title = label;
    const kbd = el.querySelector("kbd");
    if (kbd) kbd.textContent = label;
  });
}

// With their deck known (from RoyaleAPI or set by you), their next cards are simply the
// ones they haven't played yet. "Doesn't match" drops it and goes back to predictions.
function renderAssumedDeck(s, opp) {
  const seen = new Set(opp.revealed);
  const slot = (c) => opp.cards.indexOf(c);
  const key = (c) => (coach ? s.hotkeys.coach_opp : s.hotkeys.opp_seen)[slot(c)];
  $("pred-mode").textContent = opp.deck_source === "history" ? "· their deck from RoyaleAPI" : "· the deck you set";
  const rest = opp.cards.filter((c) => !seen.has(c));
  $("pred-cards").innerHTML = `
    <div class="inline"><span class="muted small">${rest.length ? `${rest.length} not played yet` : "All 8 played."}</span>
      <button data-cmd="clear_opp_deck" class="mismatch">Doesn't match</button></div>
    ${rest.map((c) => `
      <div class="pred" data-play='${esc(JSON.stringify({ cmd: "play", side: "opp", card: c }))}' title="Click to log as played">
        ${key(c) ? `<kbd>${esc(keyLabel(key(c)))}</kbd>` : "<span></span>"}
        <div>${esc(cardName(c))} <span class="muted small">${cards[c]?.elixir ?? ""}</span></div>
        <span></span>
      </div>`).join("")}`;
  $("pred-decks-box").hidden = true;
}

function renderPrediction(s) {
  const p = s.prediction;
  const opp = s.match.opp;
  if (opp?.deck_source) return renderAssumedDeck(s, opp);
  $("pred-decks-box").hidden = false;
  const labels = { exact: "from decks with all seen cards", similar: "from similar decks", history: "from their recent decks", none: "", complete: "all 8 known" };
  $("pred-mode").textContent = p.mode === "none" ? "" : `· ${labels[p.mode] || p.mode}${p.support ? ` (${p.support} decks)` : ""}`;
  if (!p.cards.length) {
    $("pred-cards").innerHTML = `<div class="muted small">${s.dataset.decks_loaded ? "Waiting for the opponent's first card." : "No deck data yet: run a crawl from Setup (needs an API token)."}</div>`;
  } else {
    // In coach mode a prediction's key is its slot among the opponent's 8 (after the seen cards).
    const predKey = (c, i) => coach ? s.hotkeys.coach_opp[s.coach_slots.indexOf(c.card)] : s.hotkeys.opp_predicted[i];
    $("pred-cards").innerHTML = p.cards.slice(0, 10).map((c, i) => `
      <div class="pred" data-play='${esc(JSON.stringify({ cmd: "play", side: "opp", card: c.card }))}' title="Click to log as played">
        ${predKey(c, i) ? `<kbd>${esc(keyLabel(predKey(c, i)))}</kbd>` : "<span></span>"}
        <div>${esc(cardName(c.card))} <span class="muted small">${cards[c.card]?.elixir ?? ""}</span>
          <div class="bar"><i style="width:${Math.round(c.p * 100)}%"></i></div></div>
        <div class="p">${Math.round(c.p * 100)}%</div>
      </div>`).join("");
  }
  $("pred-decks").innerHTML = p.decks.map((d) => `
    <div class="deck-row"><span class="src">${d.source === "history" ? "their deck" : "meta"} · ${Math.round(d.share * 100)}%</span><br>
    ${d.cards.map((k) => d.missing.includes(k) ? `<b>${esc(cardName(k))}</b>` : esc(cardName(k))).join(", ")}</div>`).join("")
    || `<div class="muted small">—</div>`;
}

function renderOppDeck(m) {
  const src = m.opp?.deck_source;
  $("opp-deck-label").textContent = src === "history" ? "Their deck · from their recent battles"
    : src === "manual" ? "Their deck · set by you" : "Seen cards";
  $("opp-deck-actions").hidden = m.status !== "running" || !$("opp-deck-editor").hidden;
  $("opp-deck-build").textContent = src ? "Edit their deck" : "Build their deck";
}

function openOppDeckEditor() {
  const cards = S.match.opp?.cards || [];
  $("opp-deck-inputs").innerHTML = Array.from({ length: 8 }, (_, i) =>
    `<input list="card-names" placeholder="Card ${i + 1}" value="${esc(cards[i] ? cardName(cards[i]) : "")}">`).join("");
  $("opp-deck-editor").hidden = false;
  $("opp-deck-actions").hidden = true;
  [...$("opp-deck-inputs").querySelectorAll("input")].find((i) => !i.value)?.focus();
}
function closeOppDeckEditor() { $("opp-deck-editor").hidden = true; }

function renderVision(s) {
  const v = s.vision;
  const lines = [];
  const status = {
    idle: "Press Start (or Read screen) to read the opponent's name from the phone.",
    capturing: "Taking a screenshot…", reading: "Reading the name…", resolving: "Looking up the player…",
    waiting: "No name on screen yet: waiting for the battle to show…",
    resolved: "Matched the opponent's tag.", ambiguous: "Several possible players: pick one.",
    not_found: "Couldn't find this player. Try RoyaleAPI search or paste the tag.",
  }[v.status] || "";
  if (v.error) lines.push(`<div class="status-line error">${esc(v.error)}</div>`);
  else lines.push(`<div class="status-line">${esc(status)}</div>`);
  if (v.read?.player_name) lines.push(`<div class="small">Read: <b>${esc(v.read.player_name)}</b>${v.read.clan_name ? ` · ${esc(v.read.clan_name)}` : " · no clan"}${v.read.confident ? "" : " <span class='muted'>(unsure)</span>"}</div>`);
  for (const c of v.candidates || []) {
    lines.push(`<div class="cand" data-tag="${esc(c.tag)}" data-name="${esc(c.name)}"><span>${esc(c.name)} <span class="muted">${esc(c.tag)}</span></span>
      <span class="muted">${esc(c.clan_name || "no clan")} · ${c.trophies ?? "?"} · ${Math.round(c.score)}</span></div>`);
  }
  if (v.search_url && v.status !== "resolved") lines.push(`<div class="small"><a href="${esc(v.search_url)}" target="_blank" rel="noopener" style="color:var(--elixir)">Search RoyaleAPI for this name ↗</a></div>`);
  $("vision").innerHTML = lines.join("");
  const h = s.opponent;
  $("opp-history").textContent = h.history_status ? `Their battle log: ${h.history_status}` : "";
  // Their recent decks, newest first: click one to use it.
  const current = [...(s.match.opp?.cards || [])].sort().join();
  const running = s.match.status === "running";
  $("opp-history-decks").innerHTML = (h.history_decks || []).map((d) => {
    const isCurrent = current === [...d].sort().join();
    const pick = running && !isCurrent;
    return `<div class="deck-row${pick ? " pick" : ""}${isCurrent ? " current" : ""}"${pick ? ` data-opp-deck='${esc(JSON.stringify(d))}' title="Use this deck"` : ""}>
      <span class="src">${isCurrent ? "their deck now" : pick ? "use this deck" : "recent deck"}</span><br>${d.map((k) => esc(cardName(k))).join(", ")}</div>`;
  }).join("");
}

function eventText(e) {
  switch (e.type) {
    case "start": return "Match start";
    case "end": return "Match end";
    case "play": return `${e.side === "me" ? "You" : "Opp"} played ${cardName(e.card)}`;
    case "adjust": return `${e.side === "me" ? "Your" : "Opp"} elixir ${e.amount > 0 ? "+" : ""}${e.amount}`;
    case "ability": return `${e.side === "me" ? "Your" : "Opp"} champion ability`;
    case "collector_destroyed": return `${e.side === "me" ? "Your" : "Opp"} collector destroyed`;
    case "next_phase": return "Skipped to next phase";
    default: return e.type;
  }
}

function renderTimeline(m) {
  const ev = (m.events || []).filter((e) => !(coach && e.side === "me")).reverse();
  $("timeline").innerHTML = ev.map((e) => `<li><span class="t" title="${e.game_time}s in">${fmtClock(e.clock)}</span><span class="dot ${e.side || ""}"></span><span>${esc(eventText(e))}</span></li>`).join("");
}

function renderSetup(s) {
  if (document.activeElement !== $("my-tag")) $("my-tag").value = s.my_tag || "";
  $("scrcpy-log").textContent = (s.scrcpy.log || []).join("\n");
  const c = s.crawl;
  $("dataset").textContent = `Dataset: ${s.dataset.decks} decks, ${s.dataset.battles} battles, ${s.dataset.players} players indexed, ${s.dataset.matches} matches saved.`
    + (c.running ? ` Crawling: ${c.players} players, ${c.battles} battles, ${c.queued} queued…` : "");
  $("crawl").disabled = !!c.running || !s.api.enabled;
  const crop = s.vision_crop;
  if (crop) {
    const b = $("calib-box");
    Object.assign(b.style, { left: `${crop[0] * 100}%`, top: `${crop[1] * 100}%`, width: `${(crop[2] - crop[0]) * 100}%`, height: `${(crop[3] - crop[1]) * 100}%` });
  }
  if (!renderSetup.deckFilled && Object.keys(cards).length) {
    renderSetup.deckFilled = true;
    $("deck-edit").innerHTML = Array.from({ length: 8 }, (_, i) => `<input list="card-names" data-i="${i}" value="${esc(s.my_deck[i] ? cardName(s.my_deck[i]) : "")}">`).join("");
  }
}

async function loadMatches() {
  const list = await fetch("/api/matches").then((r) => r.json());
  $("matches").innerHTML = list.map((m) => `
    <div class="match-row"><span class="${m.result || ""}">${esc(m.result || "—")}</span>
      vs ${esc(m.opp_name || "unknown")} ${m.crowns ? `(${m.crowns.join("-")})` : ""}
      <span class="muted">· ${new Date(m.started_at * 1000).toLocaleString()}</span><br>
      <span class="muted">${esc((m.opp_deck || m.opp_revealed || []).map(cardName).join(", "))}</span></div>`).join("")
    || `<div class="muted small">No matches yet.</div>`;
}

// ---------- events ----------
document.addEventListener("click", async (e) => {
  const play = e.target.closest("[data-play]");
  if (play) { await command(JSON.parse(play.dataset.play)); return; }
  const btn = e.target.closest("button[data-cmd]");
  if (btn) {
    const body = { cmd: btn.dataset.cmd };
    if (btn.dataset.side) body.side = btn.dataset.side;
    if (btn.dataset.amount) body.amount = Number(btn.dataset.amount);
    if (btn.dataset.action) body.action = btn.dataset.action;
    await command(body);
    return;
  }
  const deck = e.target.closest("[data-opp-deck]");
  if (deck) { await command({ cmd: "set_opp_deck", cards: JSON.parse(deck.dataset.oppDeck) }); return; }
  const cand = e.target.closest(".cand");
  if (cand) await command({ cmd: "set_opp_tag", tag: cand.dataset.tag, name: cand.dataset.name });
});

// Coach mode lives on the server, because the global 1–8 keys only fire while it's on.
// Your plays are still tracked if you log them.
$("coach-toggle").onclick = () => command({ cmd: "set_coach", on: !coach });

$("opp-deck-build").onclick = openOppDeckEditor;
$("opp-deck-cancel").onclick = closeOppDeckEditor;
$("opp-deck-save").onclick = async () => {
  const keys = [...document.querySelectorAll("#opp-deck-inputs input")].map((i) => byName[i.value.trim().toLowerCase()]);
  if (keys.some((k) => !k)) return toast("Every slot needs a card name from the list", true);
  if (await command({ cmd: "set_opp_deck", cards: keys })) { closeOppDeckEditor(); toast("Using that deck"); }
};

$("opp-tag-set").onclick = () => command({ cmd: "set_opp_tag", tag: $("opp-tag").value });
$("my-tag-set").onclick = () => command({ cmd: "set_my_tag", tag: $("my-tag").value }).then((r) => r && toast("Saved; loading your deck…"));
$("crawl").onclick = () => command({ cmd: "crawl", players: 200 }).then((r) => r && toast("Crawl started"));
$("deck-save").onclick = () => {
  const keys = [...document.querySelectorAll("#deck-edit input")].map((i) => byName[i.value.trim().toLowerCase()]);
  if (keys.some((k) => !k)) return toast("Every slot needs a card name from the list", true);
  command({ cmd: "set_deck", cards: keys }).then((r) => r && toast("Deck saved"));
};
document.querySelectorAll("details").forEach((d) => d.addEventListener("toggle", () => {
  if (d.open && d.querySelector("#matches")) loadMatches();
  if (d.open && d.querySelector("#calib-img")) {
    const v = Date.now();
    $("calib-img").src = `/api/vision/last_screen.png?v=${v}`;
    $("crop-img").src = `/api/vision/last_crop.png?v=${v}`;
  }
}));

// In-browser search for an unseen opponent card.
let searchHits = [], searchActive = 0;
function score(name, q) {
  const n = name.toLowerCase();
  if (n.startsWith(q)) return 3;
  if (n.split(/[\s-]/).some((w) => w.startsWith(q))) return 2;
  if (n.includes(q)) return 1;
  // loose subsequence match
  let i = 0;
  for (const ch of n) if (ch === q[i]) i++;
  return i === q.length ? 0.5 : 0;
}
function renderSearch() {
  $("opp-search-results").innerHTML = searchHits.map((k, i) =>
    `<div class="${i === searchActive ? "active" : ""}" data-card="${esc(k)}">${esc(cardName(k))} <span class="muted">${cards[k]?.elixir ?? ""}</span></div>`).join("");
}
$("opp-search").addEventListener("input", (e) => {
  const q = e.target.value.trim().toLowerCase();
  const seen = new Set(S?.match?.opp?.revealed || []);
  const prob = Object.fromEntries((S?.prediction?.cards || []).map((c) => [c.card, c.p]));  // likely cards win ties
  searchHits = !q ? [] : Object.values(cards)
    .filter((c) => !seen.has(c.key))
    .map((c) => [c.key, score(c.name, q)]).filter(([, sc]) => sc > 0)
    .sort((a, b) => b[1] - a[1] || (prob[b[0]] || 0) - (prob[a[0]] || 0) || cardName(a[0]).localeCompare(cardName(b[0])))
    .slice(0, 8).map(([k]) => k);
  searchActive = 0;
  renderSearch();
});
$("opp-search").addEventListener("keydown", async (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    searchActive = Math.max(0, Math.min(searchHits.length - 1, searchActive + (e.key === "ArrowDown" ? 1 : -1)));
    renderSearch(); e.preventDefault();
  } else if (e.key === "Enter" && searchHits[searchActive]) {
    await logSearch(searchHits[searchActive]);
  } else if (e.key === "Escape") { e.target.value = ""; searchHits = []; renderSearch(); }
});
$("opp-search-results").addEventListener("click", (e) => {
  const d = e.target.closest("[data-card]");
  if (d) logSearch(d.dataset.card);
});
async function logSearch(key) {
  const r = await command({ cmd: "play", side: "opp", card: key });
  if (r) { $("opp-search").value = ""; searchHits = []; renderSearch(); }
}

// ---------- data flow ----------
async function loadCards() {
  const list = await fetch("/api/cards").then((r) => r.json());
  cards = Object.fromEntries(list.map((c) => [c.key, c]));
  byName = Object.fromEntries(list.map((c) => [c.name.toLowerCase(), c.key]));
  $("card-names").innerHTML = list.map((c) => `<option value="${esc(c.name)}">`).join("");
}

function onState(state) {
  const prevStatus = S?.match?.status;
  S = state;
  render();
  window.onPhoneState?.(state);
  if (prevStatus === "running" && state.match.status === "ended") setTimeout(loadMatches, 500);
  if (state.messages.length) {
    const last = state.messages[state.messages.length - 1];
    if (last.t > (onState.lastMsg || 0)) {
      onState.lastMsg = last.t;
      if (Date.now() / 1000 - last.t < 5) toast(last.text, last.level !== "info");
    }
  }
  if (state.version !== lastVersion) {
    lastVersion = state.version;
    // Card list may have been refreshed from the API (icons, new cards).
    if (Object.values(state.my_deck).some((k) => !cards[k]) || Date.now() - (loadCards.at || 0) > 60000) {
      loadCards.at = Date.now();
      loadCards();
    }
  }
}

function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = (e) => onState(JSON.parse(e.data));
  ws.onclose = () => setTimeout(connect, 1000);
}

(async function init() {
  loadCards.at = Date.now();
  await loadCards();
  onState(await fetch("/api/state").then((r) => r.json()));
  connect();
})();
