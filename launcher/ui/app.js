const $ = (id) => document.getElementById(id);
let busy = false;
// must match --seam in style.css and NARROW in app.py
const NARROW = 476, LOGW = 360, WIDE = NARROW + LOGW;

let lastLog = "";
let logHad = false;
let logOpen = false;
let logStarted = false;
let userClosed = false;
// a screen switch picks up a different log source (main vs setup); its
// first render shouldn't count as "empty -> output" and auto-open
let skipAutoOpen = false;

const LOG_MS = 130, LOG_STEPS = 14;

// discrete resize steps, not per-frame -- per-frame shook the window
function stepWidth(from, to, steps, ms, done) {
  const t0 = performance.now();
  let last = -1;
  const loop = () => {
    const elapsed = performance.now() - t0;
    const step = Math.min(steps, Math.floor((elapsed / ms) * steps));
    if (step !== last) {
      last = step;
      window.pywebview.api.set_width(Math.round(from + (to - from) * step / steps));
    }
    if (elapsed < ms) requestAnimationFrame(loop);
    else if (done) done();
  };
  requestAnimationFrame(loop);
}

function setLogOpen(open) {
  logOpen = open;
  $("rail").classList.toggle("open", logOpen);
  $("logpanel").classList.toggle("open", logOpen);
  if (logOpen) stepWidth(NARROW, WIDE, LOG_STEPS, LOG_MS);
  else stepWidth(WIDE, NARROW, LOG_STEPS, LOG_MS);
}

function paintLog() {
  if (!logOpen) return;
  const box = $("logbox");
  const text = lastLog || "Nothing logged yet.";
  if (box.textContent === text) return;
  const stuck = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  box.textContent = text;
  box.classList.toggle("empty", !lastLog);
  if (stuck) box.scrollTop = box.scrollHeight;
}

function renderLog(text) {
  lastLog = text;
  const has = !!text;
  // open only on empty -> output, never after the user closed it or
  // right after switching screens to a different log source
  if (logStarted && has && !logHad && !userClosed && !skipAutoOpen) setLogOpen(true);
  logHad = has;
  logStarted = true;
  skipAutoOpen = false;
  paintLog();
}

function toggleLog() {
  setLogOpen(!logOpen);
  userClosed = !logOpen;
  paintLog();
}

function paint(s) {
  lastRunning = s.running;
  $("ver").textContent = s.version;
  $("addr").textContent = s.url;
  $("dot").className = "dot " + s.tone;
  $("label").textContent = s.label;
  $("toggle").textContent = s.running ? "STOP" : "START";
  $("toggle").disabled = busy || !s.can_start;
  $("bar").className = s.progress > 0 ? "bar on" : "bar";
  $("barfill").style.width = Math.round(s.progress * 100) + "%";
  $("hint").className = s.hint ? (s.hint_bad ? "hint on bad" : "hint on") : "hint";
  $("hint").textContent = s.hint || "";
  $("plug").textContent = s.plugin_text;
  $("plug").className = s.plugin_warn ? "ver warn" : "ver";
  $("install").textContent = s.plugin_action;
  $("install").disabled = busy;
  const setupCls = "ghost" + (s.cold ? " cold" : "");
  if ($("setupbtn").className !== setupCls) $("setupbtn").className = setupCls;
  if (document.activeElement !== $("vram")) $("vram").value = s.vram_mode;

  renderLog(s.log);
  fit();
}

async function call(action) {
  busy = true;
  $("toggle").disabled = true;
  $("install").disabled = true;
  try { paint(await action()); } finally { busy = false; }
}

$("rail").onclick = toggleLog;
$("author").onclick = (e) => {
  e.preventDefault();
  window.pywebview.api.open_url($("author").href);
};
$("toggle").onclick = () => call(window.pywebview.api.toggle_server);
$("install").onclick = () => call(window.pywebview.api.install_plugin);
$("vram").onchange = async () => {
  await window.pywebview.api.set_vram_mode($("vram").value);
  setupTick();
};

// ---- setup screen

const PATH_LABELS = {root: "Project", python: "Python",
                     aseprite_dir: "Aseprite", models_dir: "Model"};
const chosen = new Set();
let onSetup = false;
let installing = false;
let lastRunning = false;
let noteTimer = 0;

function showNote(msg) {
  const n = $("note");
  n.textContent = msg;
  n.hidden = false;
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => { n.hidden = true; fit(); }, 4500);
  fit();
}

function mark(item) {
  if (item.step_state === "running") return "…";
  if (item.step_state === "done" || item.state === "ok") return "✓";
  if (item.step_state === "failed") return "✕";
  if (item.state === "blocked") return "·";
  return "";
}

function setupPaint(s) {
  if (document.activeElement !== $("vram")) $("vram").value = s.vram_mode;
  installing = s.running;

  const rows = $("setuprows");
  rows.innerHTML = "";
  for (const item of s.items) {
    const row = document.createElement("div");
    row.className = "srow " + (item.step_state || item.state);
    const actionable = item.state !== "ok" && item.state !== "blocked";
    if (actionable && !s.running) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = chosen.has(item.id);
      box.onchange = () => {
        box.checked ? chosen.add(item.id) : chosen.delete(item.id);
        setupPaint(s);
      };
      row.appendChild(box);
    } else {
      const m = document.createElement("span");
      m.className = "mark";
      m.textContent = mark(item);
      row.appendChild(m);
    }
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.label;
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = item.step_detail || item.detail;
    row.append(name, detail);
    rows.appendChild(row);
  }

  const paths = $("setuppaths");
  paths.innerHTML = "";
  for (const [kind, label] of Object.entries(PATH_LABELS)) {
    const row = document.createElement("div");
    row.className = "prow" + (s.paths[kind] ? "" : " warn");
    const name = document.createElement("span");
    name.className = "pname";
    name.textContent = label;
    const val = document.createElement("span");
    val.className = "pval";
    val.textContent = s.paths[kind] || "not found";
    row.append(name, val);
    if ((s.overrides || []).includes(kind)) {
      const undo = document.createElement("button");
      undo.className = "ghost reset";
      undo.textContent = "↺";
      undo.title = "Reset to auto-detect";
      undo.disabled = s.running;
      undo.onclick = async () => setupPaint(await window.pywebview.api
                                              .reset_path(kind));
      row.append(undo);
    }
    const pick = document.createElement("button");
    pick.className = "ghost";
    pick.textContent = "Choose";
    pick.disabled = s.running;
    pick.onclick = async () => setupPaint(await window.pywebview.api
                                            .choose_path(kind));
    row.append(pick);
    paths.appendChild(row);
  }

  const btn = $("installbtn");
  btn.textContent = s.running ? "CANCEL"
    : (s.checking ? "CHECKING…" : "INSTALL SELECTED (" + chosen.size + ")");
  btn.disabled = !s.running && (s.checking || chosen.size === 0);

  renderLog(s.log.replace(/\\\\/g, "\\"));
  fit();
}

$("installbtn").onclick = async () => {
  const api = window.pywebview.api;
  setupPaint(await (installing ? api.cancel_install()
                               : api.install([...chosen])));
};

function showScreen(setup) {
  onSetup = setup;
  skipAutoOpen = true;
  $("main-view").hidden = setup;
  $("setup-view").hidden = !setup;
  $("setupbtn").querySelector(".gear").style.display = setup ? "none" : "";
  $("navlbl").textContent = setup ? "← Back" : "Setup";
  if (setup) {
    $("ver").textContent = "SETUP";
    $("setupbtn").className = "ghost";
    setupTick();
  } else {
    tick();
  }
}
$("setupbtn").onclick = () => {
  if (onSetup) { showScreen(false); return; }
  if (lastRunning) {
    showNote("Stop the server before opening Setup.");
    return;
  }
  $("note").hidden = true;
  showScreen(true);
};

async function setupTick() {
  if (!onSetup) return;
  setupPaint(await window.pywebview.api.setup_state());
}

async function tick() {
  if (busy || onSetup) return;
  paint(await window.pywebview.api.state());
}

let pathsBusy = false;

function push() {
  if (pathsBusy) return;
  const delta = document.body.scrollHeight - window.innerHeight;
  window.pywebview.api.resize(Math.round(delta));
}

function fit() { push(); }

const PATHS_MS = 250;
const PATHS_STEPS = 20;
const PATHS_CLOSE_ANIMATE = false;
let pathsOpen = false;

// unused fallback, kept: per-frame close, replaced by stepShrink
function followClose(ms, done) {
  const t0 = performance.now();
  const loop = () => {
    const delta = document.body.scrollHeight - window.innerHeight;
    window.pywebview.api.resize(Math.round(delta));
    if (performance.now() - t0 < ms) requestAnimationFrame(loop);
    else done();
  };
  requestAnimationFrame(loop);
}

function stepShrink(total, steps, ms, done) {
  const t0 = performance.now();
  let last = -1;
  let sent = 0;
  const loop = () => {
    const elapsed = performance.now() - t0;
    const step = Math.min(steps, Math.floor((elapsed / ms) * steps));
    if (step !== last) {
      last = step;
      const target = -Math.round(total * step / steps);
      window.pywebview.api.resize(target - sent);
      sent = target;
    }
    if (elapsed < ms) requestAnimationFrame(loop);
    else if (done) done();
  };
  requestAnimationFrame(loop);
}

// unused fallback, kept: FLIP-style ghost close
function collapsePathsGhost(wrap, h) {
  const rect = wrap.getBoundingClientRect();
  const ghost = document.createElement("div");
  ghost.className = "paths-ghost";
  ghost.style.left = rect.left + "px";
  ghost.style.top = rect.top + "px";
  ghost.style.width = rect.width + "px";
  ghost.style.height = h + "px";
  ghost.appendChild($("setuppaths").cloneNode(true));
  document.body.appendChild(ghost);

  wrap.style.transition = "none";
  wrap.style.height = "0px";
  void wrap.offsetHeight;
  wrap.style.transition = "";
  window.pywebview.api.resize(-h);
  pathsBusy = false;

  requestAnimationFrame(() => {
    ghost.style.height = "0px";
    ghost.style.opacity = "0";
  });
  setTimeout(() => ghost.remove(), PATHS_MS + 20);
}

$("pathstoggle").onclick = () => {
  pathsOpen = !pathsOpen;
  pathsBusy = true;
  $("pathstoggle").classList.toggle("open", pathsOpen);
  const wrap = $("pathswrap");
  const h = $("setuppaths").offsetHeight;
  if (pathsOpen) {
    window.pywebview.api.resize(h);
    wrap.style.height = h + "px";
    setTimeout(() => {
      if (pathsOpen) wrap.style.height = "auto";
      pathsBusy = false;
    }, PATHS_MS);
  } else if (PATHS_CLOSE_ANIMATE) {
    wrap.style.height = h + "px";
    requestAnimationFrame(() => { wrap.style.height = "0px"; });
    followClose(PATHS_MS, () => { pathsBusy = false; });
  } else {
    wrap.style.height = h + "px";
    requestAnimationFrame(() => { wrap.style.height = "0px"; });
    stepShrink(h, PATHS_STEPS, PATHS_MS, () => { pathsBusy = false; });
  }
};

function lockRailSize() {
  const rail = $("rail");
  rail.style.height = Math.round(window.innerHeight * 0.6) + "px";
}

window.addEventListener("pywebviewready", () => {
  lockRailSize();
  tick();
  setInterval(() => { onSetup ? setupTick() : tick(); }, 1000);
});
