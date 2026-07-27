const $ = (id) => document.getElementById(id);
let busy = false;

// serializes round-trip api calls; resize/set_width stay direct (no reply to race)
let apiChain = Promise.resolve();
function apiCall(fn) {
  const result = apiChain.then(fn, fn);
  apiChain = result.catch(() => {});
  return result;
}
// must match --seam in style.css and NARROW in app.py
const NARROW = 476, LOGW = 476, WIDE = NARROW + LOGW;

let lastLog = "";
let lastPainted = null;
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
  if (open === logOpen) return;  // already there: don't replay the slide
  logOpen = open;
  $("rail").classList.toggle("open", logOpen);
  $("logpanel").classList.toggle("open", logOpen);
  if (logOpen) stepWidth(NARROW, WIDE, LOG_STEPS, LOG_MS);
  else stepWidth(WIDE, NARROW, LOG_STEPS, LOG_MS);
}

function escapeHTML(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// wrap only the leading level token so ERROR/WARNING lines stand out
function logHTML(text) {
  return text.split("\n").map((line) => {
    if (line === "-- stopped --") return `<div class="stopped">${line}</div>`;
    const m = line.match(/^(ERROR|CRITICAL|WARNING|Warning)\b/);
    if (!m) return escapeHTML(line);
    const cls = /^(ERROR|CRITICAL)/.test(m[1]) ? "lvl-error" : "lvl-warn";
    return `<span class="${cls}">${m[1]}</span>` + escapeHTML(line.slice(m[1].length));
  }).join("\n");
}

function paintLog() {
  if (!logOpen) return;
  const box = $("logbox");
  const text = lastLog || "Nothing logged yet.";
  if (lastPainted === text) return;
  const stuck = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  lastPainted = text;
  if (lastLog) box.innerHTML = logHTML(text);
  else box.textContent = text;
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
  $("logstatus").textContent = "";
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
  try { paint(await apiCall(() => action())); } finally { busy = false; }
}

async function copyText(text) {
  if (await apiCall(() => window.pywebview.api.copy_to_clipboard(text))) return;
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (e) { /* fall through to execCommand */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

$("rail").onclick = toggleLog;
$("logcopy").onclick = async (e) => {
  e.stopPropagation();
  await copyText(lastLog || "");
  const btn = $("logcopy");
  btn.textContent = "✓";
  setTimeout(() => { btn.textContent = "⧉"; }, 1200);
};
$("author").onclick = (e) => {
  e.preventDefault();
  apiCall(() => window.pywebview.api.open_url($("author").href));
};
$("toggle").onclick = () => call(window.pywebview.api.toggle_server);
$("install").onclick = () => call(window.pywebview.api.install_plugin);
$("vram").onchange = async () => {
  await apiCall(() => window.pywebview.api.set_vram_mode($("vram").value));
  setupTick();
};

// ---- setup screen

const PATH_LABELS = {root: "Project", python: "Python",
                     aseprite_dir: "Aseprite", models_dir: "Model"};

// break only after each backslash, never mid-word ("python.\nexe")
function setPathText(el, text) {
  el.textContent = "";
  const parts = text.split("\\");
  parts.forEach((part, i) => {
    const last = i === parts.length - 1;
    el.appendChild(document.createTextNode(part + (last ? "" : "\\")));
    if (!last) el.appendChild(document.createElement("wbr"));
  });
}

const chosen = new Set();
let effective = new Set();
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
  if (item.step_state === "done" || item.state === "ok") return "✓";
  if (item.step_state === "failed") return "✕";
  if (item.state === "blocked" || item.step_state === "skipped" ||
      item.step_state === "cancelled") return "·";
  return "";
}

// chosen holds only explicit picks; needs are closed over fresh each render
function closeOverNeeds(items, base) {
  const byId = Object.fromEntries(items.map((it) => [it.id, it]));
  const result = new Set(base);
  const stack = [...base];
  while (stack.length) {
    const cur = stack.pop();
    for (const need of byId[cur]?.needs || []) {
      if (byId[need] && byId[need].state !== "ok" && !result.has(need)) {
        result.add(need);
        stack.push(need);
      }
    }
  }
  return result;
}

function setupPaint(s) {
  if (document.activeElement !== $("vram")) $("vram").value = s.vram_mode;
  installing = s.running;

  const current = s.items.find((it) => it.step_state === "running");
  $("logstatus").textContent = current ? current.label : "";

  const actionableIds = new Set(s.items
    .filter((item) => item.state !== "ok" && item.state !== "blocked")
    .map((item) => item.id));
  for (const id of [...chosen]) if (!actionableIds.has(id)) chosen.delete(id);
  effective = closeOverNeeds(s.items, chosen);

  const rows = $("setuprows");
  rows.innerHTML = "";
  let optionalShown = false;
  s.items.forEach((item, i) => {
    if (!item.required && !optionalShown) {
      optionalShown = true;
      const div = document.createElement("div");
      div.className = "divider";
      div.innerHTML = '<i class="short"></i><span>Optional</span><i class="long"></i>';
      rows.appendChild(div);
    }
    const row = document.createElement("div");
    row.className = "srow " + (item.step_state || item.state);
    const actionable = item.state !== "ok" && item.state !== "blocked";
    const explicit = chosen.has(item.id);
    const isEffective = effective.has(item.id);
    if (actionable && !item.step_state && s.running) {
      // queued for this run, waiting its turn
      const m = document.createElement("span");
      m.className = "mark";
      m.textContent = isEffective ? "•" : "";
      row.appendChild(m);
    } else if (actionable && !item.step_state && isEffective && !explicit) {
      // pulled in only because another pick needs it, not a toggle of its own
      const m = document.createElement("span");
      m.className = "mark linked";
      m.textContent = "•";
      m.title = "Needed by another selected step";
      row.appendChild(m);
    } else if (actionable && !item.step_state) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = explicit;
      box.onchange = () => {
        box.checked ? chosen.add(item.id) : chosen.delete(item.id);
        setupPaint(s);
      };
      row.appendChild(box);
    } else {
      const m = document.createElement("span");
      m.className = "mark" + (item.step_state === "running" ? " spin" : "");
      if (item.step_state === "running") {
        // a fresh element every poll must resume mid-spin, not snap to 0deg
        m.style.animationDelay = `-${performance.now() % 700}ms`;
      } else {
        m.textContent = mark(item);
      }
      row.appendChild(m);
    }
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = item.label;
    const detail = document.createElement("span");
    detail.className = "detail";
    detail.textContent = item.step_state ? item.step_detail : item.detail;
    row.append(name, detail);
    rows.appendChild(row);
  });

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
    setPathText(val, s.paths[kind] || "not found");
    if (s.paths[kind]) {
      val.title = "Open in Explorer";
      val.onclick = () => apiCall(() => window.pywebview.api.open_path(s.paths[kind]));
    }
    row.append(name, val);
    if ((s.overrides || []).includes(kind)) {
      const undo = document.createElement("button");
      undo.className = "ghost reset";
      undo.textContent = "↺";
      undo.title = "Reset to auto-detect";
      undo.disabled = s.running;
      undo.onclick = async () => setupPaint(await apiCall(() =>
        window.pywebview.api.reset_path(kind)));
      row.append(undo);
    }
    const pick = document.createElement("button");
    pick.className = "ghost";
    pick.textContent = "Choose";
    pick.disabled = s.running;
    pick.onclick = async () => setupPaint(await apiCall(() =>
      window.pywebview.api.choose_path(kind)));
    row.append(pick);
    paths.appendChild(row);
  }

  const btn = $("installbtn");
  btn.textContent = s.running ? "CANCEL"
    : (s.checking ? "CHECKING…" : "INSTALL SELECTED (" + effective.size + ")");
  btn.disabled = !s.running && (s.checking || chosen.size === 0);

  renderLog(s.log.replace(/\\\\/g, "\\"));
  fit();
}

$("installbtn").onclick = async () => {
  const api = window.pywebview.api;
  setupPaint(await apiCall(() => installing ? api.cancel_install()
                                             : api.install([...effective])));
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
  setupPaint(await apiCall(() => window.pywebview.api.setup_state()));
}

async function tick() {
  if (busy || onSetup) return;
  paint(await apiCall(() => window.pywebview.api.state()));
}

let pathsBusy = false;

function push() {
  if (pathsBusy) return;
  const delta = document.body.scrollHeight - window.innerHeight;
  window.pywebview.api.resize(Math.round(delta));
}

function fit() { push(); }

const PATHS_MS = 140, PATHS_STEPS = 14;
let pathsOpen = false;

// same idea as stepWidth: window and content move in lockstep, no CSS transition
function stepHeight(from, to, steps, ms, wrap, done) {
  const t0 = performance.now();
  let last = -1;
  const loop = () => {
    const elapsed = performance.now() - t0;
    const step = Math.min(steps, Math.floor((elapsed / ms) * steps));
    if (step !== last) {
      const prev = last < 0 ? from : from + (to - from) * last / steps;
      const cur = from + (to - from) * step / steps;
      window.pywebview.api.resize(Math.round(cur - prev));
      wrap.style.height = Math.round(cur) + "px";
      last = step;
    }
    if (elapsed < ms) requestAnimationFrame(loop);
    else if (done) done();
  };
  requestAnimationFrame(loop);
}

$("pathstoggle").onclick = () => {
  pathsOpen = !pathsOpen;
  pathsBusy = true;
  $("pathstoggle").classList.toggle("open", pathsOpen);
  const wrap = $("pathswrap");
  const h = $("setuppaths").offsetHeight;
  if (pathsOpen) {
    stepHeight(0, h, PATHS_STEPS, PATHS_MS, wrap, () => {
      if (pathsOpen) wrap.style.height = "auto";
      pathsBusy = false;
    });
  } else {
    stepHeight(h, 0, PATHS_STEPS, PATHS_MS, wrap, () => { pathsBusy = false; });
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
