// Dashdown AI edit panel (dashdown serve --edit)
//
// Self-gates on the #dashdown-edit config script — the server injects it only
// on dev-server, full-shell, authed renders, so this module is a no-op in
// builds, embeds, and plain `dashdown serve`. The panel posts the author's
// request to the edit API (token header), streams the agent's transcript over
// the edit WebSocket, and SURVIVES the live-reloads the agent's own file
// saves trigger: state persists to sessionStorage on every event, and after a
// reload the panel re-opens, reconnects, and replays — deduping by (run_id,
// seq). The dashboard visibly updating mid-run IS the feature.
//
// Agent output is untrusted text: rendered via textContent, never innerHTML.

"use strict";

import { parseUrlParams } from "../core.js";

const STORAGE_KEY = "dashdown:edit-panel";
const MAX_STORED_LINES = 400;

let cfg = null; // {token, agent, available, probe, permission_mode}
let ws = null;
let panel = null; // DOM refs {root, transcript, textarea, runBtn, stopBtn, status, result}
let state = {
  open: false,
  draft: "",
  runId: null,
  lastSeq: 0,
  running: false,
  resumeAvailable: false,
  lines: [], // [{kind, text}] — persisted transcript
};

function readConfig() {
  const el = document.getElementById("dashdown-edit");
  if (!el) return null;
  try {
    return JSON.parse(el.textContent || "null");
  } catch (e) {
    console.error("dashdown: failed to parse edit config", e);
    return null;
  }
}

function saveState() {
  try {
    const s = { ...state, lines: state.lines.slice(-MAX_STORED_LINES) };
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(s));
  } catch (e) {
    /* storage full/blocked — panel still works, it just won't survive reload */
  }
}

function loadState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) state = { ...state, ...JSON.parse(raw) };
  } catch (e) {
    /* ignore */
  }
}

/* ------------------------------------------------------------------ *
 * Transcript rendering (textContent only)                             *
 * ------------------------------------------------------------------ */
function addLine(kind, text, { persist = true } = {}) {
  if (persist) {
    state.lines.push({ kind, text });
    if (state.lines.length > MAX_STORED_LINES) state.lines.shift();
    saveState();
  }
  if (!panel) return;
  const div = document.createElement("div");
  div.className = `dashdown-edit-line dashdown-edit-line-${kind}`;
  div.textContent = text;
  panel.transcript.appendChild(div);
  panel.transcript.scrollTop = panel.transcript.scrollHeight;
}

function renderStoredLines() {
  panel.transcript.textContent = "";
  for (const l of state.lines) addLine(l.kind, l.text, { persist: false });
}

function setStatus(text, running) {
  state.running = !!running;
  if (!panel) return;
  panel.status.textContent = text || "";
  panel.runBtn.hidden = !!running;
  panel.stopBtn.hidden = !running;
  panel.root.classList.toggle("dashdown-edit-running", !!running);
  saveState();
}

/* ------------------------------------------------------------------ *
 * Event handling                                                      *
 * ------------------------------------------------------------------ */
function handleEvent(runId, seq, event) {
  if (runId === state.runId && seq <= state.lastSeq) return; // replay dedupe
  if (runId !== state.runId) {
    state.runId = runId;
    state.lastSeq = 0;
  }
  state.lastSeq = seq;
  saveState();

  const t = event.type;
  if (t === "status") {
    if (event.state === "running") setStatus(`${cfg.agent} is working…`, true);
  } else if (t === "text") {
    addLine("text", event.text);
  } else if (t === "tool") {
    addLine("tool", event.target ? `⚙ ${event.name} · ${event.target}` : `⚙ ${event.name}`);
  } else if (t === "raw") {
    addLine("raw", event.line);
  } else if (t === "error") {
    addLine("error", `✗ ${event.message}`);
    setStatus("Failed", false);
  } else if (t === "result") {
    handleResult(event);
  }
}

function handleResult(r) {
  state.resumeAvailable = !!r.resume_available;
  const secs = r.duration_ms != null ? ` in ${(r.duration_ms / 1000).toFixed(1)}s` : "";
  if (r.ok) {
    addLine("done", `✓ Done${secs}`);
    setStatus("Done — type a follow-up below", false);
  } else {
    const reason = r.reason || `exit code ${r.exit_code}`;
    addLine("error", `✗ ${r.state} (${reason})${secs}`);
    if (r.stderr_tail) addLine("raw", r.stderr_tail);
    setStatus("Failed", false);
  }
  const touched = [].concat(r.changed_files || [], r.created_files || [], r.deleted_files || []);
  if (touched.length) addLine("files", `Files: ${touched.join(", ")}`);
  if (r.config_changed)
    addLine("warn", "⚠ dashdown.yaml / sources.yaml changed — review before trusting this dashboard's config.");
  if (r.verify && r.verify.ok === false)
    addLine("error", `✗ project no longer loads: ${r.verify.error}`);
  if (r.truncated) addLine("warn", "transcript truncated (ring buffer cap)");
  if (panel) {
    panel.undoBtn.hidden = !r.undo_available;
    panel.undoBtn.dataset.runId = state.runId;
  }
}

/* ------------------------------------------------------------------ *
 * Networking                                                          *
 * ------------------------------------------------------------------ */
function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/_dashdown/ws/edit`;
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(wsUrl());
  } catch (e) {
    addLine("error", "✗ could not open the edit socket");
    return;
  }
  ws.onopen = () => ws.send(JSON.stringify({ token: cfg.token }));
  ws.onmessage = (msg) => {
    let data;
    try {
      data = JSON.parse(msg.data);
    } catch (e) {
      return;
    }
    if (data.protocol) {
      // hello: {protocol, active}. If nothing is active and we thought one
      // was, the run finished while we were away (replay carries the result).
      return;
    }
    if (data.run_id && data.event) handleEvent(data.run_id, data.seq, data.event);
  };
  ws.onclose = () => {
    ws = null;
    // Reconnect only while a run is (believed) active — the socket is idle
    // chrome otherwise.
    if (state.running) setTimeout(connect, 1000);
  };
}

async function post(path, body) {
  const resp = await fetch(`/_dashdown/api/edit/${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Dashdown-Edit-Token": cfg.token,
    },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    /* non-JSON error body */
  }
  return { ok: resp.ok, status: resp.status, data };
}

async function startRun() {
  const prompt = panel.textarea.value.trim();
  if (!prompt || state.running) return;
  connect();
  addLine("prompt", `➤ ${prompt}`);
  setStatus("Starting…", true);
  panel.undoBtn.hidden = true;
  const { ok, status, data } = await post("run", {
    prompt,
    page: window.location.pathname,
    params: parseUrlParams(),
    resume: state.resumeAvailable,
  });
  if (!ok) {
    if (status === 409 && data && data.run_id) {
      // Another tab's run — attach to it (the socket replays its transcript).
      state.runId = data.run_id;
      state.lastSeq = 0;
      setStatus("Attached to the already-running edit…", true);
      return;
    }
    addLine("error", `✗ ${(data && data.detail) || `run failed (${status})`}`);
    setStatus("Failed", false);
    return;
  }
  state.runId = data.run_id;
  state.lastSeq = 0;
  panel.textarea.value = "";
  state.draft = "";
  saveState();
}

async function stopRun() {
  if (!state.runId) return;
  await post("cancel", { run_id: state.runId });
}

async function undoRun() {
  const runId = panel.undoBtn.dataset.runId;
  if (!runId || state.running) return;
  const { ok, data } = await post("undo", { run_id: runId });
  if (!ok) {
    addLine("error", `✗ undo failed: ${(data && data.detail) || "unknown error"}`);
    return;
  }
  const n = (data.restored || []).length + (data.deleted || []).length;
  addLine("done", `↩ Undone — ${n} file(s) restored. The page will reload.`);
  panel.undoBtn.hidden = true;
  saveState();
}

/* ------------------------------------------------------------------ *
 * DOM                                                                 *
 * ------------------------------------------------------------------ */
function buildPanel() {
  const root = document.createElement("aside");
  root.className = "dashdown-edit-panel";
  root.hidden = true;

  const header = document.createElement("div");
  header.className = "dashdown-edit-header";
  const title = document.createElement("span");
  title.textContent = cfg.available ? `Edit with AI · ${cfg.agent}` : "Edit with AI";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "dashdown-edit-close";
  closeBtn.setAttribute("aria-label", "Close edit panel");
  closeBtn.textContent = "✕";
  header.append(title, closeBtn);

  const transcript = document.createElement("div");
  transcript.className = "dashdown-edit-transcript";

  const status = document.createElement("div");
  status.className = "dashdown-edit-status";

  const footer = document.createElement("div");
  footer.className = "dashdown-edit-footer";
  const textarea = document.createElement("textarea");
  textarea.className = "dashdown-edit-input";
  textarea.rows = 3;
  textarea.placeholder = "Describe the change — e.g. “add a bar chart of revenue by region below the table”";
  const buttons = document.createElement("div");
  buttons.className = "dashdown-edit-buttons";
  const runBtn = document.createElement("button");
  runBtn.type = "button";
  runBtn.className = "btn btn-primary btn-sm";
  runBtn.textContent = "Run";
  const stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.className = "btn btn-sm";
  stopBtn.textContent = "Stop";
  stopBtn.hidden = true;
  const undoBtn = document.createElement("button");
  undoBtn.type = "button";
  undoBtn.className = "btn btn-ghost btn-sm";
  undoBtn.textContent = "↩ Undo";
  undoBtn.hidden = true;
  buttons.append(runBtn, stopBtn, undoBtn);
  footer.append(textarea, buttons);

  root.append(header, transcript, status, footer);

  if (!cfg.available) {
    const setup = document.createElement("div");
    setup.className = "dashdown-edit-setup";
    setup.textContent = cfg.probe || "No coding-agent CLI found.";
    transcript.replaceWith(setup);
    textarea.disabled = true;
    runBtn.disabled = true;
  }

  const fab = document.createElement("button");
  fab.type = "button";
  fab.className = "dashdown-edit-fab";
  fab.setAttribute("aria-label", "Edit this dashboard with AI");
  fab.title = "Edit with AI";
  fab.innerHTML =
    '<svg fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path stroke-linecap="round" stroke-linejoin="round" ' +
    'd="M16.9 3.9a2.1 2.1 0 013 3L8.5 18.3 4 19.6l1.3-4.5L16.9 3.9z"/></svg>';

  document.body.append(fab, root);

  const setOpen = (open) => {
    state.open = open;
    root.hidden = !open;
    fab.classList.toggle("dashdown-edit-fab-open", open);
    if (open) textarea.focus();
    saveState();
  };
  fab.addEventListener("click", () => setOpen(root.hidden));
  closeBtn.addEventListener("click", () => setOpen(false));
  runBtn.addEventListener("click", startRun);
  stopBtn.addEventListener("click", stopRun);
  undoBtn.addEventListener("click", undoRun);
  textarea.addEventListener("input", () => {
    state.draft = textarea.value;
    saveState();
  });
  textarea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") startRun();
  });

  return { root, transcript, textarea, runBtn, stopBtn, undoBtn, status, setOpen };
}

export function initEditPanel() {
  cfg = readConfig();
  if (!cfg) return;
  loadState();
  panel = buildPanel();
  if (cfg.available) renderStoredLines();
  panel.textarea.value = state.draft || "";
  if (state.open) panel.setOpen(true);
  if (state.running) {
    // Mid-run reload (the agent saved a file → live reload). Reconnect; the
    // replay + seq dedupe restore whatever we missed.
    setStatus(`${cfg.agent} is working…`, true);
    connect();
  } else {
    setStatus("", false);
  }
}
