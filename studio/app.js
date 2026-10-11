// Fallboard (the 3D video studio): edits a video_style dict, asks the local server
// (video_studio.py) for preview frames, short clips and video renders.
"use strict";

const $ = (id) => document.getElementById(id);
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) throw new Error(d.error || r.statusText);
  return d;
};

// seconds per frame at 1080p on this machine (measured, RTX 4060), scaled by picture size
const PER_FRAME = { flat: 0.4, fast: 0.85, balanced: 1.2, high: 2.0, max: 2.6 };
const RES_SCALE = { 720: 0.7, 1080: 1.0, 1440: 1.6 };
const QUALITY_HINT = {
  flat: "Plain colours, no lighting: for checking timing",
  fast: "Full look without ray-traced reflections",
  balanced: "The full look",
  high: "Adds motion blur and more samples",
  max: "Most samples: slowest",
};
const PREVIEW_SHORT = 720;          // preview frames: the short side, in pixels

const FMT = {
  pct: (v) => `${Math.round(v * 100)}%`,
  x: (v) => `${v.toFixed(1)}×`,
  cm: (v) => `${(v * 100).toFixed(1)} cm`,
  mm: (v) => `${(v * 1000).toFixed(1)} mm`,
  s: (v) => `${v.toFixed(1)} s`,
  int: (v) => `${Math.round(v)}`,
  sgn: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}`,
  cmps: (v) => `${Math.round(v * 100)} cm/s`,
  f1: (v) => v.toFixed(1),
  deg: (v) => `${v >= 0 ? "+" : ""}${Math.round(v)}°`,
};
// typing a slider's value: [shown = stored × k, unit]
const UNITS = { pct: [100, "%"], cm: [100, "cm"], mm: [1000, "mm"], cmps: [100, "cm/s"], s: [1, "s"], x: [1, "×"], deg: [1, "°"] };

const S = {
  spec: [], groups: [], presets: {}, saved: [],
  style: {}, base: {}, baseName: "Classic", baseKind: "preset",
  tab: "Notes", song: null, info: null, t: 10,
  undo: [], redo: [],
  previews: [], lookingAt: 0, trash: null, shownPreview: 0, wantedPreview: 0, shownClip: 0, wantedClip: 0,
  autoTimer: null, lastRenderKey: null,
};

// ----- small helpers ---------------------------------------------------------
function fmtTime(t, frac = true) {
  const neg = t < 0;
  t = Math.abs(t);
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${neg ? "-" : ""}${m}:${(frac ? s.toFixed(1) : Math.floor(s).toString()).padStart(frac ? 4 : 2, "0")}`;
}
function fmtDur(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${s.toString().padStart(2, "0")} s` : `${s} s`;
}
const hex = (c) => "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
const unhex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const clone = (x) => structuredClone(x);
const typing = () => ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement?.tagName) &&
  !["range", "checkbox"].includes(document.activeElement.type);

// a message at the bottom; `action` ({label, fn}) adds a button, e.g. Undo
function toast(msg, err = false, action = null) {
  const t = $("toast");
  $("toastText").textContent = msg;
  t.className = "toast" + (err ? " err" : "");
  const b = $("toastAction");
  b.hidden = !action;
  if (action) {
    b.textContent = action.label;
    b.onclick = () => { t.hidden = true; action.fn(); };
  }
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), err ? 6000 : action ? 6000 : 3000);
}
const undoAction = { label: "Undo", fn: () => undo() };

// drop-down menus: one open at a time, closed by a click elsewhere or Escape
function menu(btnId, menuId) {
  $(btnId).onclick = (e) => {
    e.stopPropagation();
    const open = $(menuId).hidden;
    closeMenus();
    if (open) { if (menuId === "resetMenu") updateResetMenu(); $(menuId).hidden = false; }
  };
  $(menuId).onclick = (e) => { if (e.target.closest("button")) closeMenus(); };
}
function closeMenus() { document.querySelectorAll(".menu").forEach((m) => (m.hidden = true)); }
document.addEventListener("click", (e) => { if (!e.target.closest(".menu")) closeMenus(); });

// frame shape -> width x height for a short side
function resFor(short) {
  const shape = $("shape").value;
  const long = Math.round((short * 16) / 9 / 2) * 2;
  return shape === "9:16" ? `${short}x${long}` : shape === "1:1" ? `${short}x${short}` : `${long}x${short}`;
}

// ----- style and undo ----------------------------------------------------------
function useStyle(name, kind) {
  const src = kind === "preset" ? S.presets[name] : S.saved.find((s) => s.name === name)?.style;
  if (!src) return;
  pushUndo();
  S.base = clone(src);
  S.style = clone(src);
  S.baseName = name;
  S.baseKind = kind;
  refreshAll();
  changed(true);
}

// the preview and render options "Start over" changes (kept in its undo step)
const OPTION_IDS = ["view", "shape", "pquality", "quality", "fps", "res", "range", "from", "to"];

function snapshot(withOptions = false) {
  const snap = { style: clone(S.style), base: clone(S.base), baseName: S.baseName, baseKind: S.baseKind, tab: S.tab };
  if (withOptions) snap.options = { ...Object.fromEntries(OPTION_IDS.map((id) => [id, $(id).value])), auto: $("auto").checked };
  return snap;
}
function pushUndo(withOptions = false) {
  S.undo.push(snapshot(withOptions));
  if (S.undo.length > 150) S.undo.shift();
  S.redo = [];
  updateUndoButtons();
}
function restore(snap) {
  S.style = snap.style; S.base = snap.base; S.baseName = snap.baseName; S.baseKind = snap.baseKind;
  if (snap.options) {
    for (const id of OPTION_IDS) $(id).value = snap.options[id];
    $("auto").checked = snap.options.auto;
    S.tab = snap.tab;
    document.querySelectorAll(".part").forEach((e) => (e.hidden = $("range").value !== "part"));
    setBoxShape($("shape").value);
    updateEstimate();
  }
  refreshAll();
  changed(true);
}
function undo() {
  if (!S.undo.length) return;
  const snap = S.undo.pop();
  S.redo.push(snapshot(!!snap.options));
  restore(snap);
  updateUndoButtons();
}
function redo() {
  if (!S.redo.length) return;
  const snap = S.redo.pop();
  S.undo.push(snapshot(!!snap.options));
  restore(snap);
  updateUndoButtons();
}
function updateUndoButtons() {
  $("undo").disabled = !S.undo.length;
  $("redo").disabled = !S.redo.length;
}

function refreshAll() {
  renderStyleBar();
  renderTabs();
  renderControls();
  drawTimeline();
}

function renderStyleBar() {
  const chip = (name, kind, style) => {
    const b = document.createElement("span");
    b.className = "chip" + (kind === "saved" ? " saved" : "") + (S.baseName === name && S.baseKind === kind ? " on" : "");
    if (kind === "preset") {
      // a dot in the look's two note colours
      const sw = document.createElement("span");
      sw.className = "sw";
      const a = styleRGB("R", style), c = styleRGB("L", style);
      sw.style.background = style.color_mode === "rainbow"
        ? "conic-gradient(#ff5050, #ffd050, #50ff80, #50b0ff, #c050ff, #ff5050)" : `linear-gradient(135deg, ${a} 50%, ${c} 50%)`;
      sw.style.boxShadow = `0 0 ${4 + 3 * Math.min(2, style.glow || 0)}px ${a}`;
      b.append(sw);
    }
    b.append(name);
    b.title = kind === "preset" ? `The ${name} look` : `Your saved style "${name}"`;
    b.onclick = () => useStyle(name, kind);
    return b;
  };
  $("presets").replaceChildren(...Object.entries(S.presets).map(([n, st]) => chip(n, "preset", st)));
  $("saved").replaceChildren(...S.saved.map((s) => chip(s.name, "saved", s.style)));
  const el = $("styleName");
  el.textContent = S.baseName;
  if (!same(S.style, S.base)) {
    const m = document.createElement("span");
    m.className = "mod";
    m.textContent = "edited";
    el.append(m);
  }
  $("deleteStyle").hidden = S.baseKind !== "saved";
}

// ----- settings list -------------------------------------------------------------
const visible = (sp) => !sp.modes || sp.modes.includes(S.style.color_mode);
const isChanged = (sp) => !same(S.style[sp.id], S.base[sp.id]);
const searchingNow = () => !!$("search").value.trim() || $("changedOnly").checked;
function currentShown() {
  const q = $("search").value.trim().toLowerCase();
  const changedOnly = $("changedOnly").checked;
  if (!q && !changedOnly) return S.spec.filter((sp) => sp.group === S.tab && visible(sp));
  return S.spec.filter((sp) => visible(sp) && (!changedOnly || isChanged(sp)) &&
    (!q || `${sp.label} ${sp.desc || ""} ${sp.group}`.toLowerCase().includes(q)));
}

function renderTabs() {
  const searching = !!$("search").value.trim() || $("changedOnly").checked;
  $("tabs").classList.toggle("searching", searching);
  $("tabs").replaceChildren(...S.groups.map((g) => {
    const t = document.createElement("div");
    t.className = "tab" + (g === S.tab ? " on" : "");
    t.textContent = g;
    const n = S.spec.filter((sp) => sp.group === g && isChanged(sp)).length;
    if (n) {
      const d = document.createElement("span");
      d.className = "dot";
      d.textContent = n;
      t.title = `${n} changed`;
      t.append(d);
    }
    t.onclick = () => { S.tab = g; renderTabs(); renderControls(); saveLast(); };
    return t;
  }));
}

function renderControls() {
  const box = $("controls");
  const scroll = box.scrollTop;
  box.replaceChildren();
  const q = $("search").value.trim().toLowerCase();
  const changedOnly = $("changedOnly").checked;
  let shown = [];
  if (q || changedOnly) {
    // across every group, labelled by group
    for (const g of S.groups) {
      const hits = S.spec.filter((sp) => sp.group === g && visible(sp) && (!changedOnly || isChanged(sp)) &&
        (!q || `${sp.label} ${sp.desc || ""} ${g}`.toLowerCase().includes(q)));
      if (!hits.length) continue;
      shown.push(...hits);
      const h = document.createElement("div");
      h.className = "groupLabel";
      h.textContent = g;
      box.append(h, ...hits.map(control));
    }
    if (!shown.length) box.innerHTML = `<div class="empty-list">${changedOnly && !q ? "Nothing changed yet" : "No setting matches"}</div>`;
  } else {
    shown = S.spec.filter((sp) => sp.group === S.tab && visible(sp));
    box.append(...shown.map(control));
    box.scrollTop = scroll;
  }
  renderControlsHead(shown, !!(q || changedOnly));
}

// above the list: what is shown, how much of it is changed, and a button to put it back
function renderControlsHead(shown, searching) {
  const head = $("controlsHead");
  const changedHere = shown.filter(isChanged);
  const title = document.createElement("span");
  title.className = "grow";
  title.innerHTML = searching ? `<b>${shown.length}</b> setting${shown.length === 1 ? "" : "s"}` : `<b></b>`;
  if (!searching) title.firstChild.textContent = S.tab;
  if (changedHere.length) title.append(` · ${changedHere.length} changed`);
  const btn = document.createElement("button");
  btn.className = "btn tiny ghost";
  btn.textContent = searching ? "Reset these" : "Reset tab";
  btn.title = `Put ${searching ? "the settings listed" : `the ${S.tab} settings`} back to ${S.baseName}'s values`;
  btn.hidden = !changedHere.length;
  btn.onclick = () => resetSettings(changedHere.map((sp) => sp.id), searching ? "Those settings" : `The ${S.tab} tab`);
  head.replaceChildren(title, btn);
}

// ----- resetting ---------------------------------------------------------------------
function resetSettings(ids, what) {
  ids = ids.filter((id) => !same(S.style[id], S.base[id]));
  if (!ids.length) return toast("Nothing to reset there");
  pushUndo();
  for (const id of ids) S.style[id] = clone(S.base[id]);
  refreshAll();
  changed(true);
  toast(`${what} reset to ${S.baseName} (${ids.length} setting${ids.length === 1 ? "" : "s"})`, false, undoAction);
}

function updateResetMenu() {
  const tabIds = S.spec.filter((sp) => sp.group === S.tab && isChanged(sp)).length;
  const all = S.spec.filter(isChanged).length;
  $("resetTabLabel").textContent = `Reset the ${S.tab} tab` + (tabIds ? ` (${tabIds})` : "");
  $("resetStyleLabel").textContent = `Reset all edits` + (all ? ` (${all})` : "");
  document.querySelector('[data-reset="tab"]').disabled = !tabIds;
  document.querySelector('[data-reset="style"]').disabled = !all;
}

// "Start over": the Embers look and every preview/render option at its default (songs, styles, frames and videos stay)
function factoryReset() {
  if (!confirm("Start over with the Embers look and the default preview and render options?\n\nYour songs, saved styles, preview frames and videos are kept, and Undo brings your settings back.")) return;
  pushUndo(true);
  S.base = clone(S.presets.Embers);
  S.style = clone(S.base);
  S.baseName = "Embers";
  S.baseKind = "preset";
  S.tab = S.groups[0];
  for (const id of ["view", "shape", "pquality", "quality", "fps", "res", "range"]) {
    const sel = $(id);
    sel.value = [...sel.options].find((o) => o.defaultSelected)?.value ?? sel.options[0].value;
  }
  $("from").value = $("to").value = "";
  document.querySelectorAll(".part").forEach((e) => (e.hidden = true));
  $("auto").checked = true;
  $("search").value = "";
  $("changedOnly").checked = false;
  $("fname").dataset.touched = "";
  setBoxShape($("shape").value);
  refreshAll();
  updateFileName(true);
  updateEstimate();
  changed(true);
  toast("Started over with Embers", false, undoAction);
}

function control(sp) {
  const id = sp.id;
  const wrap = document.createElement("div");
  wrap.className = "ctl" + (isChanged(sp) ? " changed" : "");
  wrap.title = "Double-click to reset to the style's value";
  const head = document.createElement("div");
  head.className = "head";
  const lab = document.createElement("span");
  lab.className = "lab";
  lab.textContent = sp.label;
  head.append(lab);
  wrap.append(head);
  const resetOne = () => {
    if (same(S.style[id], S.base[id])) return;
    pushUndo();
    S.style[id] = clone(S.base[id]);
    refreshAll();
    changed(true);
  };
  const undoOne = document.createElement("button");
  undoOne.className = "undoOne";
  undoOne.textContent = "↺";
  undoOne.title = `Back to ${S.baseName}'s value`;
  undoOne.onclick = (e) => { e.preventDefault(); e.stopPropagation(); resetOne(); };
  const set = (v, rerender = false) => {
    if (same(v, S.style[id])) return;
    pushUndo();
    S.style[id] = v;
    wrap.classList.toggle("changed", !same(v, S.base[id]));
    renderStyleBar();
    renderTabs();
    if (rerender) renderControls();
    else renderControlsHead(currentShown(), searchingNow());
    changed(true);
  };
  wrap.ondblclick = (e) => {
    if (e.target.tagName === "INPUT" && ["text", "number"].includes(e.target.type)) return;
    resetOne();
  };

  if (sp.kind === "slider") {
    const val = document.createElement("span");
    val.className = "val";
    val.title = "Click to type a value";
    const f = FMT[sp.fmt] || ((v) => v.toFixed(2));
    val.textContent = f(S.style[id]);
    head.append(val, undoOne);
    const r = document.createElement("input");
    r.type = "range";
    r.min = sp.min; r.max = sp.max;
    r.step = sp.step || (sp.max - sp.min) / 200;
    r.value = S.style[id];
    const before = { v: S.style[id] };
    r.onpointerdown = () => (before.v = S.style[id]);
    r.oninput = () => { val.textContent = f(+r.value); S.style[id] = +r.value; wrap.classList.toggle("changed", !same(+r.value, S.base[id])); };
    r.onchange = () => { const v = +r.value; S.style[id] = before.v; set(v); before.v = v; };
    // typing an exact value, in the units shown
    val.onclick = () => {
      const [k, unit] = UNITS[sp.fmt] || [1, ""];
      const inp = document.createElement("input");
      inp.type = "number";
      inp.className = "valEdit";
      inp.step = "any";
      inp.value = +(S.style[id] * k).toFixed(4);
      const u = document.createElement("span");
      u.className = "unit";
      u.textContent = unit;
      val.replaceWith(inp, u);
      inp.focus();
      inp.select();
      let done = false;
      const finish = (keep) => {
        if (done) return;
        done = true;
        inp.remove(); u.remove();
        undoOne.before(val);
        const v = +inp.value / k;
        if (keep && inp.value !== "" && isFinite(v)) {
          const c = Math.min(sp.max, Math.max(sp.min, sp.fmt === "int" ? Math.round(v) : v));
          r.value = c;
          val.textContent = f(c);
          set(c);
          if (c !== v) toast(`${sp.label}: kept between ${f(sp.min)} and ${f(sp.max)}`);
        }
      };
      inp.onkeydown = (e) => { if (e.key === "Enter") finish(true); else if (e.key === "Escape") finish(false); e.stopPropagation(); };
      inp.onblur = () => finish(true);
    };
    wrap.append(r);
    if (sp.lo || sp.hi) {
      const ends = document.createElement("div");
      ends.className = "ends";
      ends.innerHTML = `<span>${sp.lo || ""}</span><span>${sp.hi || ""}</span>`;
      wrap.append(ends);
    }
  } else if (sp.kind === "color") {
    const row = document.createElement("div");
    row.className = "colorRow";
    const c = document.createElement("input");
    c.type = "color";
    c.value = hex(S.style[id]);
    const t = document.createElement("span");
    t.className = "dim";
    t.textContent = c.value;
    c.oninput = () => (t.textContent = c.value);
    c.onchange = () => set(unhex(c.value));
    row.append(c, t);
    head.append(undoOne);
    wrap.append(row);
  } else if (sp.kind === "choice") {
    const seg = document.createElement("div");
    seg.className = "seg";
    for (const [v, label] of sp.options) {
      const b = document.createElement("button");
      b.textContent = label;
      b.className = S.style[id] === v ? "on" : "";
      b.onclick = () => set(v, true);
      seg.append(b);
    }
    head.append(undoOne);
    wrap.append(seg);
  } else if (sp.kind === "text") {
    const t = document.createElement("input");
    t.type = "text";
    t.value = S.style[id] || "";
    t.placeholder = "(empty: none)";
    t.onchange = () => set(t.value);
    head.append(undoOne);
    wrap.append(t);
  } else if (sp.kind === "toggle") {
    // label, reset button and checkbox on one line
    const l = document.createElement("label");
    l.className = "switch";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !!S.style[id];
    cb.onchange = () => set(cb.checked);
    lab.remove();
    lab.className = "";
    l.append(lab, undoOne, cb);
    head.append(l);
    head.style.marginBottom = "0";
  }
  if (sp.desc && sp.kind !== "text") {
    const d = document.createElement("div");
    d.className = "desc";
    d.textContent = sp.desc;
    wrap.append(d);
  }
  return wrap;
}

// any change: remember it, and preview it after a pause
function changed(user) {
  saveLast();
  updateFileName();
  drawTimeline();
  if (user && $("auto").checked && S.song) {
    clearTimeout(S.autoTimer);
    S.autoTimer = setTimeout(preview, 900);
  }
}

// ----- song and timeline -------------------------------------------------------------
async function loadSongs(select) {
  const st = await api("/api/state");
  const sel = $("song");
  sel.replaceChildren();
  const groups = {};
  for (const s of st.songs) (groups[s.folder] ||= []).push(s);
  for (const [folder, songs] of Object.entries(groups)) {
    const og = document.createElement("optgroup");
    og.label = folder;
    for (const s of songs) {
      const o = document.createElement("option");
      o.value = s.path;
      o.textContent = s.name;
      og.append(o);
    }
    sel.append(og);
  }
  if (select) sel.value = select;
  return st;
}

async function chooseSong(path) {
  if (!path) return;
  S.song = path;
  $("song").value = path;
  $("songMeta").textContent = "Loading…";
  try {
    S.info = await api("/api/song?path=" + encodeURIComponent(path));
  } catch (e) {
    $("songMeta").textContent = "";
    return toast("Could not open that song: " + e.message, true);
  }
  const i = S.info;
  $("songMeta").textContent = `${fmtTime(i.duration, false)} · ${i.notes} notes · ${i.hands.length === 2 ? "both hands" : i.hands[0] === "R" ? "right hand" : "left hand"}`;
  if (S.t > i.duration || S.t <= 0) S.t = Math.min(i.duration, i.first + 8);
  setTime(S.t);
  updateFileName(true);
  updateEstimate();
  saveLast();
  loadHistory();
}

function setTime(t) {
  if (!S.info) return;
  S.t = Math.max(0, Math.min(S.info.duration, Math.round(t * 10) / 10));
  $("timeLabel").textContent = fmtTime(S.t);
  $("durLabel").textContent = fmtTime(S.info.duration, false);
  drawTimeline();
}

function styleRGB(hand, st = S.style) {
  const c = st.color_mode === "fire" ? (hand === "R" ? st.fire_right : st.fire_left)
    : st.color_mode === "single" ? st.single_color
    : st.color_mode === "hand" ? (hand === "R" ? st.right_color : st.left_color)
    : hand === "R" ? [255, 206, 84] : [120, 170, 255];
  return `rgb(${c.join(",")})`;
}

function drawTimeline() {
  const cv = $("density");
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w) return;
  const dpr = window.devicePixelRatio || 1;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.scale(dpr, dpr);
  g.clearRect(0, 0, w, h);
  if (!S.info) return;
  const dur = S.info.duration || 1;
  const d = S.info.density || {};
  const n = (d.R || d.L || []).length || 1;
  const peak = Math.max(1, ...(d.R || [0]), ...(d.L || [0]));
  const mid = h / 2, bw = w / n;
  // render range
  if ($("range").value === "part") {
    const a = (+$("from").value || 0) / dur * w, b = (+$("to").value || dur) / dur * w;
    g.fillStyle = "rgba(235, 230, 220, .07)";
    g.fillRect(a, 0, Math.max(1, b - a), h);
  }
  for (const [hand, sign] of [["R", -1], ["L", 1]]) {
    const arr = d[hand] || [];
    g.fillStyle = styleRGB(hand);
    g.globalAlpha = 0.85;
    arr.forEach((v, i) => {
      if (!v) return;
      const bh = Math.max(1.5, (v / peak) * (mid - 3));
      g.fillRect(i * bw + 0.5, sign < 0 ? mid - bh : mid, Math.max(1, bw - 1), bh);
    });
  }
  g.globalAlpha = 1;
  g.fillStyle = "rgba(255,255,255,.08)";
  g.fillRect(0, mid - 0.5, w, 1);
  // previews already rendered, and the cursor
  g.fillStyle = "rgba(255,255,255,.35)";
  for (const p of S.previews) if (!p.song || p.song === S.song) g.fillRect((p.t / dur) * w - 0.5, h - 4, 1.5, 4);
  const x = (S.t / dur) * w;
  g.fillStyle = "#ebe6dc";
  g.fillRect(x - 1, 0, 2, h);
}

function timelineAt(ev) {
  const r = $("density").getBoundingClientRect();
  return ((ev.clientX - r.left) / r.width) * (S.info?.duration || 0);
}

// ----- preview frames and clips ----------------------------------------------------------
async function preview() {
  if (!S.song) return toast("Pick a song first", true);
  clearTimeout(S.autoTimer);
  const d = await api("/api/preview", {
    song: S.song, t: S.t, style: S.style, view: $("view").value, quality: $("pquality").value, res: resFor(PREVIEW_SHORT),
    shape: $("shape").value,
  });
  S.wantedPreview = d.id;
  S.pendingStyle = clone(S.style);
  S.pendingShape = $("shape").value;
  poll();
}

async function clip() {
  if (!S.song) return toast("Pick a song first", true);
  const d = await api("/api/clip", {
    song: S.song, t: S.t, style: S.style, view: $("view").value, quality: $("pquality").value, res: resFor(PREVIEW_SHORT), seconds: 3,
  });
  S.wantedClip = d.id;
  toast("Rendering a 3-second clip (about 1½ minutes)…");
  poll();
}

function setBoxShape(shape) {
  const box = $("previewBox");
  box.classList.toggle("upright", shape === "9:16");
  box.classList.toggle("square", shape === "1:1");
}

const QUALITY_NAME = { flat: "flat", fast: "fast", balanced: "balanced" };
const songName = (path) => (path || "").split(/[\\/]/).pop().replace(/\.midi?$/i, "");

function showImage(p) {
  $("clipVideo").pause();
  $("clipVideo").hidden = true;
  const img = $("previewImg");
  img.src = p.url;
  img.hidden = false;
  $("previewEmpty").hidden = true;
  setBoxShape(p.shape);
  S.lookingAt = p.id;
  $("fullBtn").hidden = $("delShown").hidden = false;
  $("previewBadge").hidden = false;
  $("previewBadge").textContent = `${fmtTime(p.t)} · frame`;
  const other = p.song && S.song && p.song !== S.song ? ` · ${songName(p.song)}` : "";
  const when = p.time ? ` · ${new Date(p.time * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : "";
  $("previewNote").textContent = `Frame at ${fmtTime(p.t)}${other} · ${QUALITY_NAME[p.quality] || "fast"} · rendered in ${p.seconds} s${when}`;
  document.querySelectorAll(".thumb").forEach((th) => th.classList.toggle("on", th.dataset.id === String(p.id)));
}

function showEmptyPreview() {
  S.lookingAt = 0;
  $("previewImg").hidden = true;
  $("previewImg").removeAttribute("src");
  $("previewBadge").hidden = true;
  $("fullBtn").hidden = $("delShown").hidden = true;
  if ($("clipVideo").hidden) $("previewEmpty").hidden = false;
  $("previewNote").textContent = "";
}

// the frames on disk for this song (or every song), newest last
async function loadHistory() {
  try {
    const q = $("allSongs").checked || !S.song ? "" : "?song=" + encodeURIComponent(S.song);
    const list = await api("/api/previews" + q);
    const trashed = new Set(S.trash?.ids || []);
    S.previews = list.filter((p) => !trashed.has(p.id));
  } catch (e) {
    S.previews = [];
  }
  renderHistory();
  drawTimeline();
  const shown = S.previews.find((p) => p.id === S.lookingAt);
  if (shown) showImage(shown);
  else if (S.previews.length && $("clipVideo").hidden) showImage(S.previews[S.previews.length - 1]);
  else if (!S.previews.length) showEmptyPreview();
}

function renderHistory() {
  const box = $("history");
  $("historyWrap").hidden = !S.previews.length;
  $("historyCount").textContent = S.previews.length;
  $("clearHistory").textContent = S.previews.length > 1 ? `Delete all ${S.previews.length}` : "Delete";
  box.replaceChildren(...S.previews.slice().reverse().map((p) => {
    const th = document.createElement("div");
    th.className = "thumb" + (p.id === S.lookingAt ? " on" : "");
    th.dataset.id = p.id;
    th.title = `${fmtTime(p.t)}${p.song && p.song !== S.song ? " · " + songName(p.song) : ""} · click to show, double-click for full size`;
    const img = document.createElement("img");
    img.src = p.url;
    img.loading = "lazy";
    const lab = document.createElement("span");
    lab.className = "lab";
    lab.textContent = fmtTime(p.t);
    const use = document.createElement("button");
    use.className = "btn tiny use";
    use.textContent = "Use look";
    use.title = "Go back to the settings this preview was made with";
    use.onclick = (e) => {
      e.stopPropagation();
      pushUndo();
      S.style = Object.assign(clone(S.style), clone(p.style));
      refreshAll();
      changed(false);
      toast("Settings restored from that preview", false, undoAction);
    };
    const del = document.createElement("button");
    del.className = "del";
    del.textContent = "✕";
    del.title = "Delete this frame";
    del.onclick = (e) => { e.stopPropagation(); deletePreviews([p.id]); };
    th.onclick = () => showImage(p);
    th.ondblclick = () => openLightbox(p.id);
    th.append(img, lab, use, del);
    return th;
  }));
}

// Deleting frames: they vanish at once, and the files go a few seconds later unless Undo is pressed
function deletePreviews(ids) {
  ids = ids.filter((id) => S.previews.some((p) => p.id === id));
  if (!ids.length) return;
  commitTrash();
  const gone = S.previews.filter((p) => ids.includes(p.id));
  const order = S.previews.map((p) => p.id);
  if (ids.includes(S.lookingAt)) {
    // show the neighbour: the next newer frame, else the next older one
    const i = order.indexOf(S.lookingAt);
    const next = order.slice(i + 1).find((id) => !ids.includes(id)) ?? order.slice(0, i).reverse().find((id) => !ids.includes(id));
    const p = S.previews.find((x) => x.id === next);
    if (p) showImage(p); else showEmptyPreview();
  }
  S.previews = S.previews.filter((p) => !ids.includes(p.id));
  S.trash = { ids, items: gone, timer: setTimeout(commitTrash, 7000) };
  renderHistory();
  drawTimeline();
  if ($("lightbox").open) {
    const p = S.previews.find((x) => x.id === S.lookingAt);
    if (p) openLightbox(p.id); else $("lightbox").close();
  }
  toast(ids.length === 1 ? "Frame deleted" : `${ids.length} frames deleted`, false, { label: "Undo", fn: restoreTrash });
}

function commitTrash() {
  if (!S.trash) return;
  clearTimeout(S.trash.timer);
  const ids = S.trash.ids;
  S.trash = null;
  api("/api/previews/delete", { ids }).catch((e) => toast("Couldn't delete: " + e.message, true));
}

function restoreTrash() {
  if (!S.trash) return;
  clearTimeout(S.trash.timer);
  S.previews = [...S.previews, ...S.trash.items].sort((a, b) => a.id - b.id);
  const last = S.trash.items[S.trash.items.length - 1];
  S.trash = null;
  renderHistory();
  drawTimeline();
  showImage(last);
  toast("Restored");
}

function deleteAllPreviews() {
  const n = S.previews.length;
  if (!n) return;
  const where = $("allSongs").checked ? "from every song" : `of ${S.info?.title || "this song"}`;
  if (n > 1 && !confirm(`Delete all ${n} preview frames ${where}?`)) return;
  deletePreviews(S.previews.map((p) => p.id));
}

// full-size view of a frame, stepping through the history
function openLightbox(id) {
  const p = S.previews.find((x) => x.id === id);
  if (!p) return;
  showImage(p);
  $("lightImg").src = p.url;
  const i = S.previews.indexOf(p);
  $("lightName").textContent = `${fmtTime(p.t)} · ${i + 1} of ${S.previews.length}`;
  $("lightPrev").disabled = i === 0;
  $("lightNext").disabled = i === S.previews.length - 1;
  if (!$("lightbox").open) $("lightbox").showModal();
}
function stepLightbox(d) {
  const i = S.previews.findIndex((x) => x.id === S.lookingAt);
  const p = S.previews[i + d];
  if (p) openLightbox(p.id);
}

function showPreviewState(p, c) {
  const pBusy = p.state === "running" || p.state === "queued" || p.waiting;
  const cBusy = c.state === "running";
  $("previewBusy").hidden = !(pBusy || cBusy);
  $("cancelClip").hidden = !cBusy;
  if (cBusy) $("previewBusyText").textContent = `Rendering clip… ${Math.round(c.elapsed || 0)} s`;
  else if (pBusy) $("previewBusyText").textContent = p.state === "running" ? `Rendering frame… ${Math.round(p.elapsed || 0)} s` : "Waiting…";
  // only frames this page asked for (the rest come from the history on disk)
  if (p.state === "done" && S.wantedPreview && p.id > S.shownPreview && p.id >= S.wantedPreview && !S.previews.find((x) => x.id === p.id)) {
    const entry = {
      id: p.id, url: p.url, t: p.t, seconds: p.seconds, style: S.pendingStyle || clone(S.style),
      shape: S.pendingShape || $("shape").value, song: S.song, quality: $("pquality").value, time: Date.now() / 1000,
    };
    S.previews.push(entry);
    S.shownPreview = p.id;
    showImage(entry);
    renderHistory();
    drawTimeline();
  } else if (p.state === "error" && S.wantedPreview && p.id > S.shownPreview) {
    S.shownPreview = p.id;
    toast("Preview failed: " + p.error, true);
  }
  if (c.state === "done" && S.wantedClip && c.id > S.shownClip && c.id >= S.wantedClip) {
    S.shownClip = c.id;
    const v = $("clipVideo");
    $("previewImg").hidden = true;
    $("previewEmpty").hidden = true;
    $("fullBtn").hidden = $("delShown").hidden = true;
    S.lookingAt = 0;
    document.querySelectorAll(".thumb.on").forEach((th) => th.classList.remove("on"));
    setBoxShape($("shape").value);
    v.src = c.url;
    v.hidden = false;
    v.play();
    $("previewBadge").hidden = false;
    $("previewBadge").textContent = `${fmtTime(c.t)} · 3 s clip (looping)`;
    $("previewNote").textContent = `Clip from ${fmtTime(c.t)} · rendered in ${c.seconds} s · click a thumbnail to go back to frames`;
  } else if (c.state === "error" && S.wantedClip && c.id > S.shownClip) {
    S.shownClip = c.id;
    toast("Clip failed: " + c.error, true);
  }
}

// ----- render -----------------------------------------------------------------
function frameCount() {
  if (!S.info) return 0;
  const fps = +$("fps").value;
  let a = S.info.first - 3, b = S.info.duration + 2;
  if ($("range").value === "part") {
    a = +$("from").value || 0;
    b = +$("to").value || S.info.duration;
  }
  return Math.max(0, Math.round((b - a) * fps));
}

function updateEstimate() {
  const n = frameCount();
  const extra = 1 + 0.25 * (S.style.extra_hands || 0);       // each extra pair of hands costs a little
  const per = PER_FRAME[$("quality").value] * (RES_SCALE[$("res").value] || 1) * extra;
  $("estimate").textContent = n ? `${n.toLocaleString()} frames · about ${fmtDur(n * per + 60)}` : "";
  $("resNote").textContent = `${resFor(+$("res").value).replace("x", " × ")} px`;
  $("qualityHint").textContent = QUALITY_HINT[$("quality").value] || "";
}

function updateFileName(force) {
  if (!S.info) return;
  const f = $("fname");
  const auto = `${S.info.title} - ${S.baseName}${same(S.style, S.base) ? "" : " (edited)"}${$("shape").value === "9:16" ? " (upright)" : $("shape").value === "1:1" ? " (square)" : ""}`;
  if (force || !f.dataset.touched) f.value = auto;
}

async function render() {
  if (!S.song) return toast("Pick a song first", true);
  const body = {
    song: S.song, style: S.style, quality: $("quality").value, fps: +$("fps").value, res: resFor(+$("res").value),
    view: $("view").value, name: $("fname").value.trim(),
  };
  if ($("range").value === "part") { body.start = $("from").value; body.end = $("to").value; }
  try {
    const d = await api("/api/render", body);
    toast(d.result === "queued" ? "Added to the queue" : "Rendering started");
    $("fname").dataset.touched = "";
    poll();
  } catch (e) { toast(e.message, true); }
}

function showRender(r) {
  const running = r.state === "running";
  $("progress").hidden = r.state === "idle";
  $("cancelBtn").hidden = !running;
  $("renderBtn").textContent = running ? "Add to queue" : "Render video";
  const fill = $("barFill");
  let pct = 0;
  if (running) {
    const stage = { starting: "Starting", export: "Solving the hands", audio: "Rendering the audio", frames: "Rendering frames", encode: "Making the video" }[r.stage] || r.stage;
    let txt = `${r.name}: ${stage}`;
    if (r.total) {
      pct = Math.min(100, (100 * (r.done || 0)) / r.total);
      fill.style.width = (r.stage === "encode" ? 100 : pct) + "%";
      txt += ` · ${r.done || 0} / ${r.total} frames`;
      if (r.eta !== undefined && r.stage === "frames") txt += ` · ${fmtDur(r.eta)} left (${r.per_frame} s a frame)`;
    } else fill.style.width = "2%";
    txt += ` · ${fmtDur(r.elapsed)} so far`;
    $("progText").textContent = txt;
  } else if (r.state === "done") {
    fill.style.width = "100%";
    $("progText").textContent = `Finished ${r.name}`;
  } else if (r.state === "error") {
    $("progText").textContent = "Render failed: " + (r.error || "");
  } else if (r.state === "cancelled") {
    $("progText").textContent = "Cancelled (frames already made are kept: rendering the same again resumes)";
  }
  document.title = running ? `${Math.round(pct)}% · Fallboard` : "Fallboard";
  // a small progress pill in the top bar, for when the render card is out of sight
  $("topStatus").hidden = !running;
  if (running) $("topStatusText").textContent = `Rendering ${r.total ? Math.round(pct) + "%" : "…"}${r.queue?.length ? ` · ${r.queue.length} queued` : ""}`;
  const q = r.queue || [];
  $("queue").hidden = !q.length;
  $("queue").replaceChildren(...q.map((item, i) => {
    const row = document.createElement("div");
    row.className = "item";
    row.innerHTML = `<span class="dim">Queued ${i + 1}</span><span class="grow"></span>`;
    row.children[1].textContent = `${item.name} · ${item.quality} · ${item.res} · ${item.fps} fps`;
    const rm = document.createElement("button");
    rm.className = "btn tiny ghost";
    rm.textContent = "Remove";
    rm.onclick = () => api("/api/render/dequeue", { index: i }).then(poll);
    row.append(rm);
    return row;
  }));
  const key = r.state + (r.finished || "") + (r.name || "");
  if (S.lastRenderKey && key !== S.lastRenderKey && r.state === "done") {
    toast(`${r.name} is ready`);
    loadVideos();
  }
  S.lastRenderKey = key;
}

async function loadVideos() {
  const vids = await api("/api/videos");
  $("videos").replaceChildren(...vids.map((v) => {
    const row = document.createElement("div");
    row.className = "video";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = v.name;
    const meta = document.createElement("span");
    meta.className = "dim small meta";
    meta.textContent = `${(v.size / 1e6).toFixed(0)} MB · ${new Date(v.time * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    name.title = v.name;
    const play = document.createElement("button");
    play.className = "btn small play";
    play.textContent = "Play";
    play.onclick = () => {
      $("video").src = "/video/" + encodeURIComponent(v.name);
      $("playerName").textContent = v.name;
      $("player").showModal();
      $("video").play();
    };
    const show = document.createElement("button");
    show.className = "btn small ghost show";
    show.textContent = "Show";
    show.title = "Show it in its folder";
    show.onclick = () => api("/api/open", { video: v.name });
    row.append(name, meta, play, show);
    return row;
  }));
  if (!vids.length) $("videos").innerHTML = '<span class="dim">No videos yet</span>';
}

// ----- status polling (also keeps the server from closing while the page is open)
async function poll() {
  clearTimeout(poll.timer);
  let busy = false;
  try {
    const st = await api("/api/status");
    showPreviewState(st.preview, st.clip || {});
    showRender(st.render);
    busy = st.render.state === "running" || ["running", "queued"].includes(st.preview.state) || st.preview.waiting ||
      (st.clip && st.clip.state === "running");
  } catch (e) {
    $("progText").textContent = "The studio server isn't answering (was it closed?)";
  }
  poll.timer = setTimeout(poll, busy ? 1000 : 5000);
}

// ----- remembered settings
function saveLast() {
  clearTimeout(saveLast.timer);
  saveLast.timer = setTimeout(() => api("/api/last", {
    song: S.song, style: S.style, base: S.base, baseName: S.baseName, baseKind: S.baseKind, t: S.t,
    view: $("view").value, shape: $("shape").value, pquality: $("pquality").value, quality: $("quality").value,
    fps: $("fps").value, res: $("res").value, tab: S.tab, auto: $("auto").checked, allSongs: $("allSongs").checked,
  }).catch(() => {}), 600);
}

// ----- start
async function init() {
  const st = await loadSongs();
  S.spec = st.spec;
  S.groups = st.groups;
  S.presets = st.presets;
  S.saved = st.styles;
  const last = st.last || {};
  for (const k of ["view", "shape", "pquality", "quality", "fps", "res"]) {
    if (last[k] && [...$(k).options].some((o) => o.value === String(last[k]))) $(k).value = last[k];
  }
  if (last.auto === false) $("auto").checked = false;
  if (last.allSongs) $("allSongs").checked = true;
  if (last.tab && S.groups.includes(last.tab)) S.tab = last.tab;
  const defaults = Object.fromEntries(S.spec.map((s) => [s.id, clone(s.default)]));
  const known = (o) => Object.fromEntries(Object.entries(o || {}).filter(([k]) => k in defaults));
  const base = last.baseKind === "preset" ? S.presets[last.baseName] : S.saved.find((s) => s.name === last.baseName)?.style;
  if (base) {
    S.base = Object.assign(clone(defaults), clone(base));
    S.style = Object.assign(clone(S.base), known(last.style));
    S.baseName = last.baseName;
    S.baseKind = last.baseKind;
  } else {
    S.base = clone(S.presets.Embers || defaults);
    S.style = clone(S.base);
    S.baseName = "Embers";
    S.baseKind = "preset";
  }
  if (last.t) S.t = last.t;
  refreshAll();
  setBoxShape($("shape").value);
  const song = (last.song && st.songs.find((s) => s.path === last.song)) ? last.song : st.songs[0]?.path;
  if (song) await chooseSong(song);
  updateEstimate();
  loadVideos();
  poll();
}

// ----- wiring
$("song").onchange = () => chooseSong($("song").value);
$("upload").onchange = async () => {
  const f = $("upload").files[0];
  if (!f) return;
  const r = await fetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
  const d = await r.json();
  if (!r.ok) return toast(d.error, true);
  await loadSongs(d.path);
  chooseSong(d.path);
};
{
  const cv = $("density");
  let dragging = false;
  cv.onpointerdown = (e) => { dragging = true; cv.setPointerCapture(e.pointerId); setTime(timelineAt(e)); };
  cv.onpointermove = (e) => { if (dragging) setTime(timelineAt(e)); };
  cv.onpointerup = () => { if (!dragging) return; dragging = false; saveLast(); if ($("auto").checked) preview(); };
  window.addEventListener("resize", drawTimeline);
}
$("previewBtn").onclick = preview;
$("clipBtn").onclick = clip;
$("cancelClip").onclick = () => api("/api/clip/cancel", {}).then(poll);
for (const id of ["view", "pquality"]) $(id).onchange = () => { saveLast(); if ($("auto").checked) preview(); };
$("shape").onchange = () => { setBoxShape($("shape").value); updateEstimate(); updateFileName(); saveLast(); if ($("auto").checked) preview(); };
$("auto").onchange = saveLast;
for (const id of ["quality", "fps", "res", "from", "to"]) $(id).oninput = () => { updateEstimate(); drawTimeline(); saveLast(); };
$("range").onchange = () => {
  document.querySelectorAll(".part").forEach((e) => (e.hidden = $("range").value !== "part"));
  if ($("range").value === "part" && S.info && !$("to").value) {
    $("from").value = Math.max(0, S.t - 5).toFixed(1);
    $("to").value = Math.min(S.info.duration, S.t + 10).toFixed(1);
  }
  updateEstimate();
  drawTimeline();
};
$("fname").oninput = () => ($("fname").dataset.touched = "1");
$("renderBtn").onclick = render;
$("cancelBtn").onclick = () => api("/api/render/cancel", {}).then(poll);
$("openFolder").onclick = () => api("/api/open", {});
$("closePlayer").onclick = () => { $("video").pause(); $("player").close(); };
$("helpBtn").onclick = () => $("help").showModal();
$("closeHelp").onclick = () => $("help").close();
$("undo").onclick = undo;
$("redo").onclick = redo;
$("search").oninput = () => { renderTabs(); renderControls(); };
$("changedOnly").onchange = () => { renderTabs(); renderControls(); };
$("saveStyle").onclick = async () => {
  const name = prompt("Name this style:", S.baseKind === "saved" ? S.baseName : `My ${S.baseName}`);
  if (!name) return;
  if (S.presets[name]) return toast("That's a preset's name; pick another", true);
  await api("/api/style/save", { name, style: S.style });
  S.saved = (await api("/api/state")).styles;
  S.base = clone(S.style);
  S.baseName = name;
  S.baseKind = "saved";
  refreshAll();
  saveLast();
  toast(`Saved "${name}"`);
};
$("deleteStyle").onclick = async () => {
  if (!confirm(`Delete the style "${S.baseName}"?`)) return;
  await api("/api/style/delete", { name: S.baseName });
  S.saved = (await api("/api/state")).styles;
  useStyle("Classic", "preset");
};
menu("resetBtn", "resetMenu");
menu("moreBtn", "moreMenu");
menu("appBtn", "appMenu");

// the render panel slides in from the right
function drawer(open = !document.body.classList.contains("drawer")) {
  document.body.classList.toggle("drawer", open);
}
$("renderToggle").onclick = () => drawer();
$("closeDrawer").onclick = () => drawer(false);
$("scrim").onclick = () => drawer(false);
document.querySelector('[data-reset="tab"]').onclick = () =>
  resetSettings(S.spec.filter((sp) => sp.group === S.tab).map((sp) => sp.id), `The ${S.tab} tab`);
document.querySelector('[data-reset="style"]').onclick = () => resetSettings(S.spec.map((sp) => sp.id), "Every setting");
document.querySelector('[data-reset="factory"]').onclick = factoryReset;
$("moreMenu").querySelector("label").onclick = () => setTimeout(closeMenus, 0);

// preview history
$("clearHistory").onclick = deleteAllPreviews;
$("allSongs").onchange = () => { saveLast(); loadHistory(); };
$("delShown").onclick = () => deletePreviews([S.lookingAt]);
$("fullBtn").onclick = () => openLightbox(S.lookingAt);
$("previewImg").onclick = () => openLightbox(S.lookingAt);
$("lightPrev").onclick = () => stepLightbox(-1);
$("lightNext").onclick = () => stepLightbox(1);
$("lightDelete").onclick = () => deletePreviews([S.lookingAt]);
$("closeLight").onclick = () => $("lightbox").close();
$("lightbox").onclick = (e) => { if (e.target === $("lightbox")) $("lightbox").close(); };
$("topStatus").onclick = () => drawer(true);
// frames waiting to be deleted go now if the page closes
window.addEventListener("pagehide", () => {
  if (S.trash) navigator.sendBeacon("/api/previews/delete", JSON.stringify({ ids: S.trash.ids }));
});
$("exportStyle").onclick = () => {
  const blob = new Blob([JSON.stringify({ name: S.baseName, style: S.style }, null, 1)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${S.baseName}${same(S.style, S.base) ? "" : " (edited)"}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
};
$("importStyle").onchange = async () => {
  const f = $("importStyle").files[0];
  if (!f) return;
  try {
    const d = JSON.parse(await f.text());
    const st = d.style || d;
    const known = new Set(S.spec.map((s) => s.id));
    pushUndo();
    S.style = Object.assign(Object.fromEntries(S.spec.map((s) => [s.id, clone(s.default)])),
      Object.fromEntries(Object.entries(st).filter(([k]) => known.has(k))));
    S.base = clone(S.style);
    S.baseName = d.name || f.name.replace(/\.json$/i, "");
    S.baseKind = "import";
    refreshAll();
    changed(true);
    toast(`Loaded "${S.baseName}" (Save as… to keep it in the list)`);
  } catch (e) { toast("That isn't a style file: " + e.message, true); }
  $("importStyle").value = "";
};
$("quit").onclick = async () => {
  if (!confirm("Stop the studio? A video that is rendering (and the queue) will be cancelled.")) return;
  await api("/api/quit", {}).catch(() => {});
  document.body.innerHTML = '<p style="padding:40px">The studio has stopped. Start it again from the desktop shortcut or Hand-thesia\'s menu.</p>';
};
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "z" && !typing()) { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if (mod && e.key.toLowerCase() === "y" && !typing()) { e.preventDefault(); redo(); return; }
  if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); $("saveStyle").click(); return; }
  if (typing() || mod || e.altKey) return;
  if ($("lightbox").open) {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); stepLightbox(e.key === "ArrowLeft" ? -1 : 1); }
    else if (e.key === "Delete") deletePreviews([S.lookingAt]);
    return;
  }
  if (document.querySelector("dialog[open]")) return;
  if (e.key === "Escape") { closeMenus(); drawer(false); }
  else if (e.key === "r" || e.key === "R") drawer();
  else if (e.key === " ") { e.preventDefault(); preview(); }
  else if ((e.key === "f" || e.key === "F") && S.lookingAt) openLightbox(S.lookingAt);
  else if (e.key === "Delete" && S.lookingAt) deletePreviews([S.lookingAt]);
  else if (e.key === "c" || e.key === "C") clip();
  else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    setTime(S.t + (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 5 : 1));
    clearTimeout(S.autoTimer);
    if ($("auto").checked) S.autoTimer = setTimeout(preview, 700);
    saveLast();
  } else if (e.key === "/") { e.preventDefault(); $("search").focus(); }
  else if (e.key === "?") $("help").showModal();
});

init().catch((e) => toast("Could not start: " + e.message, true));
