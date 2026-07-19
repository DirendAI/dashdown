// Dashdown Ask Integration (omnibox answer panel)
//
// The runtime operator "ask" surface, merged into the centered site-search box
// (site_search.js). There is no standalone ask box: the search input doubles as
// the ask input, and this module attaches an answer panel *under the same box*.
// Flow: the user picks the "Ask the data" row (or, in ask-only mode, just hits
// Enter); site_search.js fires a `dashdown:ask` DOM event; this module runs the
// submit — POST /_dashdown/api/ask (staged SSE) — and paints the answer.
//
// v2 design: ONE INPUT, ONE ANSWER, ONE ACTION. The panel is a glance, not a
// cockpit:
//
//   topbar   ✦ AI · <provenance, muted> · ⤢ ✕
//   answer   one or two typed sentences (form-sized server-side)
//   evidence shaped by the server's display form — a headline number card
//            ("value"), a chart with its rows folded behind a collapsed
//            "Data · N rows" disclosure ("chart"), or the open table ("table")
//   footer   [Add to page] + the follow-up input
//
// Refinement is LANGUAGE-ONLY: the follow-up field re-asks with the whole
// session as context (`history`, oldest-first — the server keeps the last few)
// and echoes the new question into the omnibox. There is no chip editor, no
// trail pill row, no per-answer options — the session history still rides
// invisibly as LLM context. (POST /_dashdown/api/ask/execute remains a
// server-side programmatic API; this client no longer calls it.)
//
// "Add to page" adds EXACTLY what the panel shows (the display form picks the
// kept elements; the panel is the preview — no menu) by POSTing to
// /_dashdown/api/ask/keep; the dev server's watcher live-reloads the page and
// page_edit.js flashes the new section. Typing an imperative ("add a KPI row
// with revenue…") in the omnibox composes NEW content instead: site_search.js
// fires `dashdown:compose`, one constrained LLM call plans validated elements,
// and a plain-words preview (Add / Cancel) confirms before anything is written.
//
// Ask is gated server-side (the box's data-config `ask` flag comes from
// `ask_enabled` — llm on ∧ ask on ∧ not embed), so this never wires up in
// static builds or embeds. The panel is only built on user interaction, so a
// headless print/screenshot run (which never asks) is untouched.

"use strict";

import {
  esc,
  parseUrlParams,
  postJson,
  readRouteParams,
  readSseFrames,
  recordsOf,
} from "../core.js";
import { currentEChartsTheme, onThemeChange } from "./echarts_theme.js";
import { updateChart } from "./chart.js";
import { setChartAnnotations } from "./annotations.js";
import { renderTableInto } from "./table.js";
import {
  relevantFilters,
  typewriterInto,
  wireAnnotationRefChips,
} from "./ask.js";

const _ASK_URL = "/_dashdown/api/ask";
const _KEEP_URL = "/_dashdown/api/ask/keep";
const _COMPOSE_URL = "/_dashdown/api/ask/compose";
const _COMPOSE_APPLY_URL = "/_dashdown/api/ask/compose/apply";

// localStorage key of the operator's recent questions (newest-first), read by
// site_search.js's empty-focus dropdown.
const _RECENT_KEY = "dashdown-recent-asks";

/**
 * Prepend a question to the recent-asks list in localStorage (dedup
 * case-insensitively, cap 8, newest-first). Any storage error — private mode,
 * quota — is swallowed; recents are a nicety, never load-bearing.
 * @param {string} question
 */
function pushRecent(question) {
  const q = (question || "").trim();
  if (!q) return;
  try {
    const raw = window.localStorage.getItem(_RECENT_KEY);
    let arr = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(arr)) arr = [];
    arr = arr.filter(
      (x) => typeof x === "string" && x.toLowerCase() !== q.toLowerCase()
    );
    arr.unshift(q);
    if (arr.length > 8) arr = arr.slice(0, 8);
    window.localStorage.setItem(_RECENT_KEY, JSON.stringify(arr));
  } catch (e) {
    /* private mode / quota — recents are best-effort */
  }
}

// Session-remembered open state of the chart form's "Data · N rows" disclosure,
// so an operator who wants rows-with-every-chart opens it once per session.
// Best-effort like the recents (private mode / quota errors are swallowed).
const _DATA_OPEN_KEY = "dashdown-ask-data-open";

function dataDisclosureOpen() {
  try {
    return window.sessionStorage.getItem(_DATA_OPEN_KEY) === "1";
  } catch (e) {
    return false;
  }
}

function rememberDataOpen(open) {
  try {
    window.sessionStorage.setItem(_DATA_OPEN_KEY, open ? "1" : "0");
  } catch (e) {
    /* best-effort */
  }
}

/**
 * Pick the headline cell for a value-form answer: the sole column, else the
 * column matching the resolved semantic metric (short-name match, mirroring the
 * server's `_find_col`), else the first numeric column, else the first cell.
 * @param {Object} payload
 * @returns {{label: string, value: *}|null}
 */
function headlineCell(payload) {
  const cols = payload.columns || [];
  const row = (payload.rows || [])[0] || [];
  if (!cols.length) return null;
  if (cols.length === 1) return { label: cols[0], value: row[0] };
  const detail = (payload.resolved || {}).detail || {};
  const metric = String(detail.metric || "");
  const short = metric.split(".").pop();
  for (let i = 0; i < cols.length; i++) {
    const c = String(cols[i]);
    if (metric && (c === metric || c.split(".").pop() === short)) {
      return { label: cols[i], value: row[i] };
    }
  }
  for (let i = 0; i < cols.length; i++) {
    if (typeof row[i] === "number") return { label: cols[i], value: row[i] };
  }
  return { label: cols[0], value: row[0] };
}

function formatHeadline(v) {
  if (typeof v === "number") {
    return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return v == null ? "—" : String(v);
}

// The display form of a payload — the server derives it; the fallback covers
// older cached payloads that predate the field.
function displayForm(payload) {
  return (
    (payload.display && payload.display.form) ||
    (payload.chart ? "chart" : "table")
  );
}

// One human line per validated compose-plan entry, for the plain-words preview
// ("what will land on the page"). Derived from the SAME plan the apply endpoint
// re-compiles, so the summary and the write can't disagree on substance.
function describePlanEntry(entry) {
  const short = (ref) => String(ref || "").split(".").pop().replace(/_/g, " ");
  const arr = (v) => (Array.isArray(v) ? v : []); // model output — never trust shape
  const el = String((entry && entry.element) || "").toLowerCase();
  if (el === "heading") return `A heading — “${entry.text || ""}”`;
  if (el === "prose") return "A short note";
  if (el === "kpi_row") {
    const names = arr(entry.metrics).map(short).join(", ");
    return `Headline numbers: ${names}`;
  }
  if (el === "value") {
    return `A headline number: ${short(entry.metric) || entry.query || ""}`;
  }
  if (el === "chart") {
    const what = entry.query
      ? String(entry.query)
      : `${short(entry.metric)} by ${short(entry.by)}`;
    // An untyped semantic chart compiles to a concrete line/bar pick
    // server-side — don't claim "auto", just say "chart".
    return `A ${entry.chart ? entry.chart + " " : ""}chart of ${what}`;
  }
  if (el === "table") {
    return `A table of ${
      entry.query ? String(entry.query) : `${short(entry.metric)} by ${short(entry.by)}`
    }`;
  }
  if (el === "list") {
    const cols = arr(entry.columns).map(short).join(", ");
    return `A list of ${cols}${entry.limit ? ` (latest ${entry.limit})` : ""}`;
  }
  return "An element";
}

/** ✦ AI badge markup — mirrors the authored ask card's provenance sparkle. */
const _AI_BADGE =
  '<span class="dashdown-ask-badge dashdown-ask-box-badge" title="AI-generated answer">' +
  '<span class="dashdown-ask-badge-text">✦ AI</span></span>';

/**
 * The current filter+route params, read lazily at submit time (Alpine stores may
 * not exist yet at init). Route params sit at lowest precedence, matching the
 * merge every data/ask request uses (core.js).
 * @returns {Object}
 */
function gatherParams() {
  const filters =
    (window.Alpine && Alpine.store && Alpine.store("filters")) || parseUrlParams();
  return { ...readRouteParams(), ...relevantFilters(filters) };
}

function mkDiv(className) {
  const d = document.createElement("div");
  d.className = className;
  return d;
}

/**
 * Wire the ask surface onto one omnibox (a `[data-async-component="site-search"]`
 * element whose config has `ask: true`). Builds the answer panel, attaches it
 * under the box, and listens for the `dashdown:ask` / `dashdown:compose` events
 * site_search.js fires.
 * @param {HTMLElement} el - The `.dashdown-site-search` wrapper.
 */
function initOne(el) {
  let config = {};
  try {
    config = JSON.parse(el.dataset.config || "{}");
  } catch (e) {
    /* keep defaults */
  }
  if (!config.ask) return; // search-only box — nothing to wire

  const input = el.querySelector(".dashdown-site-search-input");
  const results = el.querySelector(".dashdown-site-search-results");
  if (!input) return;

  // The answer panel isn't in the template (the omnibox ships as a search box);
  // build it and anchor it under the same box.
  const panel = document.createElement("div");
  panel.className = "dashdown-ask-box-panel dashdown-ask-answer-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Answer");
  panel.hidden = true;
  el.appendChild(panel);

  // A visually-hidden polite live region: screen readers hear the answer's
  // lifecycle (resolved → done → error/notice) without the typewriter stream
  // node ever being wired to aria-live (that would announce every partial
  // frame). Persists across skeleton rebuilds — resetPanel re-appends it.
  const liveRegion = document.createElement("div");
  liveRegion.className = "dashdown-visually-hidden";
  liveRegion.setAttribute("role", "status");
  liveRegion.setAttribute("aria-live", "polite");
  panel.appendChild(liveRegion);
  const announce = (msg) => {
    liveRegion.textContent = msg || "";
  };

  const MAX_TRAIL = 6; // cap the session context; drop-oldest beyond this

  let requestSeq = 0; // drop responses a newer question superseded
  let abortController = null;
  let dismissed = false; // set by close(); guards late async continuations from
  // re-opening the panel (a response landing after close never auto-opens). A
  // new submit clears it.
  let expanded = false; // panel promoted into the expand <dialog>
  let expandDialog = null; // the live <dialog> while expanded, else null
  let hasAnswer = false; // panel holds a rendered answer (for reopen)
  let suppressReopen = false; // one-shot: a programmatic input.focus() right
  // after ✕-close must not fire the reopen listener (Chromium moves focus to
  // the button on mousedown, so the omnibox focus event lands synchronously).
  // Session trail: the ask → follow-up chain, oldest-first, each entry
  // `{question, payload}`. INVISIBLE in the UI — it exists so a follow-up can
  // send the session as `history` context and so "Add to page" reads the
  // current (last) answer. A fresh header ask starts a new session.
  let trail = []; // [{question, payload}]
  // The live panel chart, so a theme toggle can dispose + re-init it (it's not
  // in chart.js's registry, so onThemeChange there won't reach it).
  let chartState = null; // { card, container, config }

  const currentEntry = () => (trail.length ? trail[trail.length - 1] : null);
  const currentPayload = () => {
    const e = currentEntry();
    return e ? e.payload : null;
  };
  const currentQuestion = () => {
    const e = currentEntry();
    return e ? e.question : "";
  };

  // Rebuilt on each answer render (buildAnswerSkeleton); later paints target
  // these stable slots instead of the whole panel.
  let slots = null; // { err, bodyWrap, chart, table, keep, followup }
  let answerBody = null; // the .dashdown-ask-body that holds the typed prose
  let followupInput = null; // the bottom "refine or follow-up" field
  let followupBusyRow = null; // slim inline busy row while a follow-up loads

  function setBusy(busy) {
    input.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function open() {
    if (panel.hidden) {
      panel.hidden = false;
      // While the answer panel is up, the search results dropdown stays hidden
      // (CSS keys off this class), so the two never stack under the box.
      el.classList.add("dashdown-ask-answer-open");
      if (results) results.hidden = true;
      input.setAttribute("aria-expanded", "true");
      // The panel was display:none while closed, so a chart initialized inside
      // it measured 0×0 — resize it now that it has a box.
      if (chartState && chartState.card && chartState.card._echarts_instance) {
        chartState.card._echarts_instance.resize();
      }
    }
  }

  function close() {
    // Abort any in-flight request and mark the panel dismissed so a response
    // that lands after this can't re-open it (the async continuations all
    // check `dismissed`). A new submit clears the flag.
    if (abortController) abortController.abort();
    setBusy(false);
    dismissed = true;
    if (expanded) collapseExpand(); // move the panel back under the omnibox
    panel.hidden = true;
    el.classList.remove("dashdown-ask-answer-open");
    input.setAttribute("aria-expanded", "false");
  }

  // Append to the session trail, capped at MAX_TRAIL (drop-oldest). Returns the
  // pushed entry (its object identity survives the slice, so a held reference —
  // e.g. the SSE `resolved` entry updated on `done` — stays valid).
  function pushTrail(entry) {
    trail.push(entry);
    if (trail.length > MAX_TRAIL) trail = trail.slice(trail.length - MAX_TRAIL);
    return entry;
  }

  // ---- Expand: promote the live panel into a modal <dialog> ---------------

  const chartInstance = () =>
    chartState && chartState.card && chartState.card._echarts_instance;

  function toggleExpand() {
    if (expanded) collapseExpand();
    else openExpand();
  }

  // Move the panel NODE itself into a top-layer <dialog> (appendChild, so its
  // state, listeners, and the chart canvas all survive), then resize the chart
  // into the taller box. Native Esc / the Collapse button restore it.
  function openExpand() {
    if (expanded) return;
    const dlg = document.createElement("dialog");
    dlg.className = "modal dashdown-ask-expand";
    dlg.setAttribute("aria-label", "Answer (expanded)");
    document.body.appendChild(dlg);
    dlg.appendChild(panel);
    expandDialog = dlg;
    expanded = true;
    updateExpandBtn();
    dlg.showModal();
    const inst = chartInstance();
    if (inst) inst.resize();
    // The bigger view shows the data rows open (a programmatic open, so the
    // operator's remembered disclosure preference is untouched).
    const details = panel.querySelector(".dashdown-ask-box-data");
    if (details) details.open = true;
    // Native close (Collapse button, Esc, or close()) moves the panel back
    // under the omnibox anchor in its original position and resizes the chart.
    dlg.addEventListener("close", () => {
      expanded = false;
      expandDialog = null;
      el.appendChild(panel);
      if (dlg.parentNode) dlg.parentNode.removeChild(dlg);
      updateExpandBtn();
      const back = chartInstance();
      if (back) back.resize();
      // Back to compact: the disclosure returns to the remembered preference.
      const det = panel.querySelector(".dashdown-ask-box-data");
      if (det) det.open = dataDisclosureOpen();
    });
  }

  function collapseExpand() {
    if (expandDialog) expandDialog.close(); // fires the close handler above
  }

  // Keep the topbar's expand button label in sync when toggling without a full
  // topbar rebuild (buildTopbar itself reads `expanded` for a fresh render).
  function updateExpandBtn() {
    const btn = panel.querySelector(".dashdown-ask-box-expand");
    if (!btn) return;
    btn.textContent = expanded ? "⤡" : "⤢";
    btn.setAttribute(
      "aria-label",
      expanded ? "Collapse answer" : "Expand answer"
    );
  }

  function disposeChart() {
    if (chartState && chartState.card && chartState.card._echarts_instance) {
      try {
        chartState.card._echarts_instance.dispose();
      } catch (e) {
        /* already disposed */
      }
    }
    chartState = null;
  }

  function resetPanel() {
    disposeChart();
    panel.innerHTML = "";
    panel.appendChild(liveRegion); // survives the rebuild (see above)
    hasAnswer = false;
    slots = null;
    answerBody = null;
    followupInput = null;
    followupBusyRow = null;
  }

  // Topbar: ✦ badge · one muted provenance line (the trust surface, truncating,
  // full text on its tooltip) · expand + close. buildTopbar reads `expanded` so
  // a rebuild while expanded keeps the Collapse affordance. `minimal` (the
  // transient loading/error/notice/compose shells) drops the badge and expand —
  // an HTTP error isn't "AI-generated" and "Thinking…" has nothing to enlarge.
  function buildTopbar(provenance, minimal) {
    const header = document.createElement("div");
    header.className = "dashdown-ask-box-topbar";
    const expandGlyph = expanded ? "⤡" : "⤢";
    const expandLabel = expanded ? "Collapse answer" : "Expand answer";
    header.innerHTML =
      (minimal ? "" : _AI_BADGE) +
      (provenance
        ? `<span class="dashdown-ask-box-topbar-prov" title="${esc(provenance)}">${esc(provenance)}</span>`
        : "") +
      '<span class="dashdown-ask-box-topbar-actions">' +
      (minimal
        ? ""
        : `<button type="button" class="dashdown-ask-box-expand" aria-label="${expandLabel}">${expandGlyph}</button>`) +
      '<button type="button" class="dashdown-ask-box-close" aria-label="Close answer">✕</button>' +
      "</span>";
    const expandBtn = header.querySelector(".dashdown-ask-box-expand");
    if (expandBtn) expandBtn.addEventListener("click", toggleExpand);
    header
      .querySelector(".dashdown-ask-box-close")
      .addEventListener("click", () => {
        close();
        // Return focus to the omnibox WITHOUT the focus listener re-opening
        // the panel we just closed (the focus event fires synchronously here).
        suppressReopen = true;
        input.focus();
        suppressReopen = false;
      });
    return header;
  }

  // Minimal shell for transient states (loading / error / notice / compose):
  // a close-only topbar, no AI badge, no expand. Resets the dialog's accessible
  // name (the compose preview overrides it after calling this).
  function panelShell() {
    resetPanel();
    panel.setAttribute("aria-label", "Answer");
    panel.appendChild(buildTopbar("", true));
    open();
    return panel;
  }

  // Full answer skeleton: topbar (with the payload's provenance) + the stable
  // slots later paints target. A muted question line rides along but is shown
  // by CSS only inside the expanded <dialog> — the compact panel sits under the
  // omnibox that already displays the question; the modal covers it.
  function buildAnswerSkeleton(payload) {
    resetPanel();
    panel.setAttribute("aria-label", "Answer");
    const provenance =
      (payload && payload.resolved && payload.resolved.kind !== "none"
        ? payload.resolved.provenance
        : "") || "";
    panel.appendChild(buildTopbar(provenance));
    const questionText = (payload && payload.question) || currentQuestion();
    if (questionText) {
      const q = mkDiv("dashdown-ask-box-question");
      q.textContent = questionText;
      panel.appendChild(q);
    }

    slots = {};
    slots.err = mkDiv(
      "dashdown-ask-error dashdown-ask-box-message dashdown-ask-box-inline-error"
    );
    slots.err.hidden = true;

    slots.bodyWrap = mkDiv("dashdown-ask-box-body-wrap");
    answerBody = mkDiv("dashdown-ask-body dashdown-ask-box-body");
    slots.bodyWrap.appendChild(answerBody);

    slots.chart = mkDiv("dashdown-ask-box-chart-slot");
    slots.table = mkDiv("dashdown-ask-box-table-slot");
    slots.keep = mkDiv("dashdown-ask-box-keep-slot");
    slots.followup = mkDiv("dashdown-ask-box-followup-slot");

    panel.appendChild(slots.err);
    panel.appendChild(slots.bodyWrap);
    panel.appendChild(slots.chart);
    panel.appendChild(slots.table);
    panel.appendChild(slots.keep);
    panel.appendChild(slots.followup);
    open();
  }

  function renderLoading() {
    panelShell();
    const body = document.createElement("div");
    body.className = "dashdown-ask-box-loading";
    body.setAttribute("role", "status");
    body.innerHTML =
      '<span class="dashdown-ask-cursor" aria-hidden="true"></span>' +
      '<span class="dashdown-ask-box-loading-text">Thinking…</span>';
    panel.appendChild(body);
  }

  // Error/notice shells deliberately DON'T set hasAnswer: an error card is not
  // worth re-showing on the next omnibox focus — Enter re-asks instead (the
  // retry path; site_search.js falls through to ask when no row is active).
  function renderError(message) {
    panelShell();
    const div = document.createElement("div");
    div.className = "dashdown-ask-error dashdown-ask-box-message";
    div.textContent = message || "Ask request failed";
    panel.appendChild(div);
    announce("Ask failed: " + (message || "Ask request failed"));
  }

  function renderNotice(message) {
    panelShell();
    const div = document.createElement("div");
    div.className = "dashdown-ask-notice dashdown-ask-box-message";
    div.textContent = message || "Ask is unavailable";
    panel.appendChild(div);
    announce(message || "Ask is unavailable");
  }

  // A follow-up keeps the current answer fully visible while it loads: no
  // loading shell, just a slim inline busy row in the follow-up slot and its
  // input disabled (reusing the loading cursor styling).
  function showFollowupBusy(busy) {
    if (!slots || !slots.followup) return;
    if (busy) {
      if (followupInput) followupInput.disabled = true;
      if (!followupBusyRow) {
        followupBusyRow = mkDiv("dashdown-ask-box-followup-busy");
        followupBusyRow.innerHTML =
          '<span class="dashdown-ask-cursor" aria-hidden="true"></span>' +
          '<span class="dashdown-ask-box-followup-busy-text">Thinking…</span>';
      }
      slots.followup.appendChild(followupBusyRow);
    } else {
      if (followupInput) followupInput.disabled = false;
      if (followupBusyRow && followupBusyRow.parentNode) {
        followupBusyRow.parentNode.removeChild(followupBusyRow);
      }
      followupBusyRow = null;
    }
  }

  // Inline (non-destructive) error slot — a follow-up failure (429 / notice /
  // network) shows here without blowing away the visible answer.
  function showInlineError(message) {
    if (!slots || !slots.err) return;
    slots.err.textContent = message || "Ask request failed";
    slots.err.hidden = false;
    announce("Ask failed: " + (message || "Ask request failed"));
  }

  // Paint (or repaint) the panel chart, then suppress the y-axis name ECharts
  // draws above the axis: the compact panel has no headroom for it and the
  // topbar + table header already name the metric. Single place, so renderChart,
  // the repaint() shim, and the theme-change re-init can't drift out of sync.
  // ONLY cartesian types have a yAxis — merging one into an axis-less chart
  // (pie/funnel/treemap, the chart-preference answers) throws inside ECharts
  // ("reading 'coordinateSystem'"), which used to knock the whole chart out
  // and drop the panel to its table fallback.
  const _CARTESIAN_TYPES = ["line", "bar", "scatter"];

  function paintPanelChart(card, records, config) {
    updateChart(card, records, config);
    if (_CARTESIAN_TYPES.includes(config.type) && card._echarts_instance) {
      card._echarts_instance.setOption({ yAxis: { name: "" } });
    }
  }

  // Build a chart host that speaks the same _chartConfig/_echarts_instance/
  // _chartInstance contract the annotation helpers expect, so setChartAnnotations
  // + emphasizeChartAnnotation work unchanged against it.
  function renderChart(payload, parent) {
    const records = recordsOf(payload);
    const spec = payload.chart || {};
    const config = {
      type: spec.type,
      x: spec.x,
      y: spec.y,
      // The generated answer title ("Channel share") headlines the chart — the
      // same text a kept section will wear, so panel and page agree.
      title: payload.title || spec.title || "",
    };
    // The server ships a concrete chart type, which skips chart.js's
    // resolveAutoConfig — where an auto-chart would derive sort_by for a temporal
    // x. So thread the server's sort hint through (temporal charts set sort_by=x)
    // or a time series renders in row order instead of by time.
    if (spec.sort_by) config.sort_by = spec.sort_by;
    // A series-split answer (by + series, e.g. "revenue by week per channel")
    // carries the splitting column — chart.js's series_by config key.
    if (spec.series_by) config.series_by = spec.series_by;
    // Value-keyed types (heatmap: cell magnitude; sankey: flow width) carry the
    // metric column under `value` — x/y are the two groupings there.
    if (spec.value) config.value = spec.value;
    const card = document.createElement("div");
    card.className = "dashdown-chart dashdown-ask-box-chart";
    card.innerHTML =
      '<div class="dashdown-chart-container dashdown-ask-box-chart-container"></div>';
    parent.appendChild(card);
    const container = card.querySelector(".dashdown-chart-container");

    const instance = echarts.init(container, currentEChartsTheme());
    card._echarts_instance = instance;
    card._chartConfig = config;
    // updateChart re-reads el._chartRecords via repaint(); the annotation helpers
    // call repaint() after mutating config.annotations.
    card._chartInstance = {
      el: card,
      config,
      echartsInstance: instance,
      repaint() {
        const recs = card._chartRecords;
        if (Array.isArray(recs) && recs.length) {
          paintPanelChart(card, recs, config);
        }
      },
    };
    chartState = { card, container, config };

    paintPanelChart(card, records, config);
    if (Array.isArray(payload.annotations) && payload.annotations.length) {
      setChartAnnotations(card, payload.annotations);
    }
    return card;
  }

  function renderTable(payload, parent) {
    const host = document.createElement("div");
    host.className = "dashdown-table dashdown-ask-box-table";
    parent.appendChild(host);
    renderTableInto(host, recordsOf(payload), {
      page_size: 10,
      export: false,
      search: false,
      fullscreen: false,
    });
  }

  // A capped result set flags how much was withheld (server: `truncated` +
  // `total_rows`). Re-rendered with each repaint, so a follow-up that returns a
  // full set drops the note.
  function appendTruncationNote(payload, parent) {
    if (!payload.truncated) return;
    const foot = mkDiv("dashdown-ask-box-truncated");
    const shown = (payload.rows && payload.rows.length) || 0;
    const total = Number(payload.total_rows);
    foot.textContent =
      `Showing first ${shown.toLocaleString()} of ` +
      `${(isFinite(total) ? total : shown).toLocaleString()} rows`;
    parent.appendChild(foot);
  }

  // The headline card for a value-form answer: the number, big, with the
  // generated title (else the metric name) as caption. No chart, no table —
  // the answer IS the number.
  function renderValueCard(payload, parent) {
    const cell = headlineCell(payload);
    if (!cell) return;
    const card = mkDiv("dashdown-ask-box-value");
    const num = document.createElement("div");
    num.className = "dashdown-ask-box-value-number";
    num.textContent = formatHeadline(cell.value);
    const label = document.createElement("div");
    label.className = "dashdown-ask-box-value-label";
    label.textContent =
      payload.title || String(cell.label).split(".").pop().replace(/_/g, " ");
    card.appendChild(num);
    card.appendChild(label);
    parent.appendChild(card);
  }

  // A row of counter cards — the "counters" display form. Two shapes: a single
  // row renders one card per NUMERIC column (a KPI set: revenue · orders ·
  // aov); a small breakdown renders one card per ROW, labeled by its first
  // non-numeric column ("as counters" on revenue by channel). Capped at 12
  // cards (the server's viability rule), full data in the disclosure below.
  function renderCountersRow(payload, parent) {
    const cols = payload.columns || [];
    const rows = payload.rows || [];
    if (!cols.length || !rows.length) return;
    const numericCol = (i) => rows.some((r) => typeof r[i] === "number");

    const wrap = mkDiv("dashdown-ask-box-counters");
    const addCard = (value, label) => {
      if (wrap.children.length >= 12) return;
      const card = mkDiv("dashdown-ask-box-counter");
      const num = mkDiv("dashdown-ask-box-counter-number");
      num.textContent = formatHeadline(value);
      const cap = mkDiv("dashdown-ask-box-counter-label");
      cap.textContent = String(label || "").split(".").pop().replace(/_/g, " ");
      card.appendChild(num);
      card.appendChild(cap);
      wrap.appendChild(card);
    };

    if (rows.length === 1) {
      cols.forEach((c, i) => {
        if (numericCol(i)) addCard(rows[0][i], c);
      });
    } else {
      const numIdx = cols.findIndex((_, i) => numericCol(i));
      const labelIdx = cols.findIndex((_, i) => i !== numIdx && !numericCol(i));
      rows.forEach((r) => {
        addCard(r[numIdx], labelIdx >= 0 ? r[labelIdx] : "");
      });
    }
    if (wrap.children.length) parent.appendChild(wrap);
  }

  // The chart form's evidence: the data table collapsed behind a native
  // <details> disclosure whose summary carries the row count (trust survives
  // the fold). The table renders lazily on first open. The open state persists
  // per session (an explicit summary click only — a programmatic open from the
  // expanded view never overwrites the operator's preference).
  function renderDataDisclosure(payload) {
    const details = document.createElement("details");
    details.className = "dashdown-ask-box-data";
    const shown = (payload.rows && payload.rows.length) || 0;
    const total = payload.truncated ? Number(payload.total_rows) : shown;
    const count = isFinite(total) ? total : shown;
    const summary = document.createElement("summary");
    summary.className = "dashdown-ask-box-data-summary";
    summary.textContent =
      `Data · ${count.toLocaleString()} row${count === 1 ? "" : "s"}`;
    details.appendChild(summary);
    const body = mkDiv("dashdown-ask-box-data-body");
    details.appendChild(body);

    let rendered = false;
    details.addEventListener("toggle", () => {
      if (!details.open || rendered) return;
      rendered = true;
      try {
        renderTable(payload, body);
        appendTruncationNote(payload, body);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
    });
    // The click fires before `open` flips, so the new state is the negation.
    // (Keyboard activation on a summary synthesizes a click, so this covers it.)
    summary.addEventListener("click", () => rememberDataOpen(!details.open));
    if (expanded || dataDisclosureOpen()) details.open = true;
    slots.table.appendChild(details);
  }

  // Rebuild the evidence slots from a payload, shaped by its display form:
  // "value" → a headline number card (with the underlying cells behind the
  // disclosure when there's more than the one headline); "chart" → the chart
  // with the table collapsed behind a "Data · N rows" disclosure; "table" → the
  // table itself, open (a list answer IS its rows). Every paint follows a
  // skeleton rebuild, so the chart is always disposed + freshly initialized.
  // Returns the chart card (or null) so the answer's annotation ref chips can
  // wire against it; a chart render failure clears its slot and falls back to
  // the open table ("Add to page" reads the DOM, so what lands matches).
  function repaintChartAndTable(payload) {
    slots.table.innerHTML = "";
    const hasData = payload.columns && payload.rows && payload.rows.length;
    const form = displayForm(payload);
    disposeChart();
    slots.chart.innerHTML = "";

    // Zero rows: one muted note, no chart/value/table chrome.
    if (!hasData) {
      if (payload.columns && payload.rows) {
        const none = mkDiv("dashdown-ask-box-nodata");
        none.textContent = "No matching data";
        slots.table.appendChild(none);
      }
      return null;
    }

    if (form === "value") {
      renderValueCard(payload, slots.chart);
      // More than the one headline cell? Keep the evidence reachable — the
      // same disclosure the chart form uses (a true 1×1 result stays bare).
      if (payload.columns.length > 1 || payload.rows.length > 1) {
        renderDataDisclosure(payload);
      }
      return null;
    }

    if (form === "counters") {
      renderCountersRow(payload, slots.chart);
      renderDataDisclosure(payload);
      return null;
    }

    let chartCard = null;
    if (form === "chart" && payload.chart) {
      try {
        chartCard = renderChart(payload, slots.chart);
      } catch (e) {
        console.error("dashdown ask box: chart render failed", e);
        // A partially-built card must not linger — elementsForKeep reads the
        // DOM to decide chart-vs-table, so the slot has to reflect the failure.
        disposeChart();
        slots.chart.innerHTML = "";
        chartCard = null;
      }
    }

    // Evidence: beside a chart the table folds into the disclosure; a
    // table-form answer (or a failed chart render) shows the table open.
    if (chartCard) {
      renderDataDisclosure(payload);
    } else {
      try {
        renderTable(payload, slots.table);
        appendTruncationNote(payload, slots.table);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
    }
    return chartCard;
  }

  // Type the answer out (the shared ask.js typewriter cadence) into `bodyEl`,
  // then swap in the sanitized answer_html and wire its ref chips.
  // `skipTypewriter` (restoring an already-read answer, e.g. compose Cancel)
  // paints the final HTML instantly.
  function renderAnswer(payload, chartCard, seq, bodyEl, skipTypewriter) {
    bodyEl.innerHTML = "";

    const finish = () => {
      bodyEl.innerHTML = payload.answer_html || esc(payload.answer_text || "");
      wireAnnotationRefChips(bodyEl, chartCard);
    };

    if (skipTypewriter) {
      finish();
      return;
    }
    typewriterInto(bodyEl, payload.answer_text || "", {
      isStale: () => dismissed || seq !== requestSeq,
      onDone: finish,
    });
  }

  // ---- Add to page ---------------------------------------------------------

  // What lands = what the panel shows — and the DOM is the truth for "shows":
  // a chart card is mounted iff a chart is on screen right now (any state-flag
  // mirror of that can desync across paints; the querySelector cannot). So
  // "Add to page" writes a chart exactly when the operator is LOOKING at one,
  // and falls back to the table only when the panel itself did. (The server's
  // `elements` enum API supports finer choices; this client deliberately
  // doesn't surface them — the panel is the preview.)
  function elementsForKeep(payload) {
    const kind = ((payload.resolved || {}).kind || "").toLowerCase();
    if (kind === "list") return undefined; // server default: the rows
    if (displayForm(payload) === "value") return ["value", "ask"];
    const chartShown = !!(
      slots &&
      slots.chart &&
      slots.chart.querySelector(".dashdown-ask-box-chart")
    );
    if (chartShown && payload.chart) return ["chart", "ask"];
    return ["table", "ask"];
  }

  const _KEEPABLE_KINDS = ["semantic", "query", "list"];

  // The one action on an answer: a single button that appends what the panel
  // shows to this page's source markdown (the dev watcher live-reloads and
  // page_edit.js flashes the new section — remove is one hover away there).
  function renderKeepFooter(payload) {
    slots.keep.innerHTML = "";
    if (!config.ask_keep) return;
    const resolved = payload.resolved || {};
    if (!_KEEPABLE_KINDS.includes(resolved.kind)) {
      // A raw-SQL answer has no named artifact to embed — say so in one muted
      // line rather than silently missing the panel's one action.
      if (resolved.kind === "sql") {
        const note = mkDiv("dashdown-ask-box-keep-note");
        note.textContent = "SQL answers can't be added to a page";
        slots.keep.appendChild(note);
      }
      return;
    }

    const footer = mkDiv("dashdown-ask-box-keep");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dashdown-ask-box-keep-btn";
    btn.textContent = "Add to page";
    const err = document.createElement("span");
    err.className = "dashdown-ask-box-keep-error";
    footer.appendChild(btn);
    footer.appendChild(err);
    slots.keep.appendChild(footer);

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      err.textContent = "";
      try {
        // Add the CURRENT answer: the last trail entry's payload is the latest
        // /ask response (follow-ups update it).
        const current = currentPayload() || payload;
        const body = {
          question: currentQuestion(),
          resolved: current.resolved,
          chart: current.chart,
          path: window.location.pathname,
        };
        // The generated title becomes the kept section's heading / chart title
        // / <Ask> prompt — the conversational question stays out of page copy
        // (the server keeps it in the section's provenance comment).
        if (current.title) body.title = current.title;
        const elements = elementsForKeep(current);
        if (elements) body.elements = elements;
        const resp = await postJson(_KEEP_URL, body);
        const data = await resp.json().catch(() => null);
        if (resp.ok && data && data.ok) {
          // Success: the server appended the section; the dev watcher will
          // live-reload shortly. Stash the id so page_edit.js can flash it.
          if (data.id) {
            try {
              window.sessionStorage.setItem("dashdown-keep-flash", data.id);
            } catch (e) {
              /* storage blocked — skip the flash */
            }
          }
          btn.textContent = "Added ✓";
          btn.classList.add("dashdown-ask-box-keep-done");
        } else {
          btn.disabled = false;
          err.textContent =
            (data && data.detail) || `Add failed (HTTP ${resp.status})`;
        }
      } catch (e) {
        btn.disabled = false;
        err.textContent = (e && e.message) || "Add failed";
      }
    });
  }

  // A slim follow-up field at the panel bottom — the ONE refinement path.
  // Enter re-asks via the normal submit flow, threading the whole session trail
  // as `history` context, and echoes the new question into the header omnibox
  // so a re-Enter re-asks it.
  function renderFollowUp() {
    slots.followup.innerHTML = "";
    const row = mkDiv("dashdown-ask-box-followup");
    const inp = document.createElement("input");
    inp.type = "text";
    inp.className = "dashdown-ask-box-followup-input";
    inp.placeholder = "Refine or ask a follow-up…";
    inp.setAttribute("aria-label", "Refine or ask a follow-up");
    row.appendChild(inp);
    slots.followup.appendChild(row);
    followupInput = inp;

    inp.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        const q = inp.value.trim();
        if (!q) return;
        // History = the whole current trail, oldest-first (the server keeps the
        // last 6) — exactly what the operator is looking at.
        const history = trail.map((t) => ({
          question: t.question,
          resolved: {
            kind: (t.payload.resolved || {}).kind,
            detail: (t.payload.resolved || {}).detail,
          },
        }));
        // Echo the new question into the header omnibox (programmatic value set
        // fires no `input` event, so the panel isn't closed by the input handler).
        input.value = q;
        submit(q, { history });
      } else if (ev.key === "Escape") {
        // While expanded the panel lives in a modal <dialog>: let Escape fall
        // through to the dialog's native cancel/close — don't preventDefault it
        // or steal focus to the omnibox that sits outside the modal.
        if (expanded) return;
        // First Escape inside the follow-up field only steps out to panel scope
        // (focus the omnibox input); a second Escape then closes the panel via
        // the capture-phase handler on `el`.
        ev.preventDefault();
        ev.stopPropagation();
        input.focus();
      }
    });
  }

  // ---- Compose: "add …" typed in the omnibox ------------------------------

  // Restore whatever answer the operator was looking at (compose borrows the
  // panel; cancel must never strand them on a blank shell). `dismissed` may be
  // true here (an Esc-close + reopen on the way), which would starve the
  // typewriter's isStale gate — clear it, and paint instantly (the answer was
  // already read once; re-typing it is noise).
  function restoreCurrentAnswer() {
    const entry = currentEntry();
    if (entry) {
      dismissed = false;
      renderAnswerPayload(entry.payload, ++requestSeq, { skipTypewriter: true });
    } else {
      close();
    }
  }

  function renderComposeLoading(instruction) {
    panelShell();
    const body = document.createElement("div");
    body.className = "dashdown-ask-box-loading";
    body.setAttribute("role", "status");
    body.innerHTML =
      '<span class="dashdown-ask-cursor" aria-hidden="true"></span>' +
      '<span class="dashdown-ask-box-loading-text">Planning “' +
      esc(instruction) +
      "”…</span>";
    panel.appendChild(body);
  }

  // Preview → confirm, in plain words: one line per element that will land,
  // then Add / Cancel. The server re-validates the echoed plan on apply, so the
  // preview and the write can't disagree.
  function renderComposePreview(instruction, data) {
    panelShell();
    panel.setAttribute("aria-label", "Add to page preview");
    hasAnswer = true;
    const wrap = mkDiv("dashdown-ask-compose");
    const head = mkDiv("dashdown-ask-compose-head");
    head.textContent = "✎ Add to page";
    const instr = mkDiv("dashdown-ask-compose-instruction");
    instr.textContent = instruction;
    wrap.appendChild(head);
    wrap.appendChild(instr);

    const list = document.createElement("ul");
    list.className = "dashdown-ask-compose-summary";
    const plan = data.plan || {};
    const sections = plan.sections || [];
    // The dropped entries came through one JSON round-trip, so identity can't
    // match — compare serialized content (both copies parse from the same
    // response text, so key order and stringification agree).
    const droppedKeys = new Set(
      (Array.isArray(data.dropped) ? data.dropped : []).map((d) =>
        JSON.stringify(d.entry)
      )
    );
    // The plan-level title compiles into a `##` heading — a substantive element
    // the summary must mention.
    if (plan.title) {
      const li = document.createElement("li");
      li.textContent = `A heading — “${plan.title}”`;
      list.appendChild(li);
    }
    for (const entry of sections) {
      if (droppedKeys.has(JSON.stringify(entry))) continue;
      const li = document.createElement("li");
      li.textContent = describePlanEntry(entry);
      list.appendChild(li);
    }
    // The server refuses all-dropped plans, so an empty list here can only be
    // over-matching on duplicate entries — keep the preview honest anyway.
    if (!list.children.length) {
      const li = document.createElement("li");
      li.textContent = "The planned content";
      list.appendChild(li);
    }
    wrap.appendChild(list);

    if (droppedKeys.size) {
      const note = mkDiv("dashdown-ask-compose-dropped");
      note.textContent =
        droppedKeys.size === 1
          ? "1 item couldn't be added (not found in your data)"
          : `${droppedKeys.size} items couldn't be added (not found in your data)`;
      wrap.appendChild(note);
    }

    const controls = mkDiv("dashdown-ask-compose-controls");
    const applyBtn = document.createElement("button");
    applyBtn.type = "button";
    applyBtn.className = "dashdown-ask-box-keep-btn dashdown-ask-compose-apply";
    applyBtn.textContent = "Add to page";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "dashdown-ask-compose-cancel";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", restoreCurrentAnswer);
    const err = document.createElement("span");
    err.className = "dashdown-ask-box-keep-error";
    controls.appendChild(applyBtn);
    controls.appendChild(cancel);
    controls.appendChild(err);
    wrap.appendChild(controls);
    panel.appendChild(wrap);
    announce("Preview ready — confirm to add to the page");

    applyBtn.addEventListener("click", async () => {
      applyBtn.disabled = true;
      err.textContent = "";
      try {
        const resp = await postJson(_COMPOSE_APPLY_URL, {
          instruction,
          plan: data.plan,
          path: window.location.pathname,
        });
        const out = await resp.json().catch(() => null);
        if (resp.ok && out && out.ok) {
          // The dev watcher live-reloads the page; page_edit.js flashes the new
          // section via the shared sessionStorage key (best-effort).
          if (out.id) {
            try {
              window.sessionStorage.setItem("dashdown-keep-flash", out.id);
            } catch (e) {
              /* storage blocked — skip the flash */
            }
          }
          applyBtn.textContent = "Added ✓";
          applyBtn.classList.add("dashdown-ask-box-keep-done");
          announce("Added to the page");
        } else {
          applyBtn.disabled = false;
          err.textContent =
            (out && out.detail) || `Add failed (HTTP ${resp.status})`;
        }
      } catch (e) {
        applyBtn.disabled = false;
        err.textContent = (e && e.message) || "Add failed";
      }
    });
  }

  // POST the instruction for a plan+preview. Same seq/abort discipline as ask.
  async function submitCompose(instruction) {
    dismissed = false;
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    setBusy(true);
    renderComposeLoading(instruction);
    try {
      const resp = await postJson(
        _COMPOSE_URL,
        { instruction, path: window.location.pathname },
        { signal: controller.signal }
      );
      if (dismissed || seq !== requestSeq) return;
      const data = await resp.json().catch(() => null);
      if (dismissed || seq !== requestSeq) return;
      setBusy(false);
      if (!data) {
        renderError(`HTTP ${resp.status}`);
        return;
      }
      if (data.notice) {
        renderNotice(data.notice);
        return;
      }
      if (!resp.ok || !data.plan) {
        renderError(
          data.detail || data.error || `Compose failed (HTTP ${resp.status})`
        );
        return;
      }
      renderComposePreview(instruction, data);
    } catch (error) {
      if (
        dismissed ||
        seq !== requestSeq ||
        (error && error.name === "AbortError")
      ) {
        return;
      }
      setBusy(false);
      console.error("dashdown ask box: compose failed", error);
      renderError((error && error.message) || "Compose failed");
    }
  }

  // ---- Answer assembly + the staged SSE consumer --------------------------

  function applyModelTooltip(payload) {
    // Model attribution on the ✦ badge tooltip — the same trust affordance the
    // authored ask cards carry.
    if (payload.model) {
      const badge = panel.querySelector(".dashdown-ask-box-badge");
      if (badge) {
        badge.setAttribute("title", `AI-generated answer · ${payload.model}`);
      }
    }
  }

  function renderAnswerPayload(payload, seq, opts = {}) {
    buildAnswerSkeleton(payload);
    applyModelTooltip(payload);
    // Answer-first hierarchy: the operator asked a question, so the answer text
    // is the headline. The chart must render before renderAnswer so the
    // answer's annotation ref chips have a chart host.
    const chartCard = repaintChartAndTable(payload);
    renderAnswer(payload, chartCard, seq, answerBody, opts.skipTypewriter);
    renderKeepFooter(payload);
    renderFollowUp();
    hasAnswer = true;
    announce("Answer ready");
  }

  // A wait state for the answer body while the commentary streams in: the same
  // blinking cursor the loading state uses, held above the already-painted
  // evidence until the `done` event arrives.
  function renderAnswerWaiting(bodyEl) {
    bodyEl.innerHTML =
      '<span class="dashdown-ask-cursor" aria-hidden="true"></span>';
  }

  // Consume a staged SSE ask response (POST /api/ask with stream:true). Two
  // events: `resolved` paints the full panel skeleton with the answer body in a
  // wait state; `done` merges the commentary into the trail entry, wires chart
  // annotations, and typewriters the answer in. `error` (after headers)
  // surfaces a fresh ask's error card, or — for a follow-up — the inline error
  // slot without blowing away the current answer. `dismissed`/`requestSeq` are
  // re-checked on every event so a superseded or closed panel never paints.
  async function consumeAskStream(response, question, seq, fresh) {
    let entry = null; // this ask's trail entry (pushed on `resolved`)
    let chartCard = null;

    await readSseFrames(response, {
      isStale: () => dismissed || seq !== requestSeq,
      onEvent: (event, data) => {
        if (event === "resolved") {
          if (dismissed || seq !== requestSeq) return;
          // New trail entry for this ask (a fresh header ask already cleared
          // the trail; a follow-up appends). Only the `resolved` event rebuilds
          // the skeleton — a follow-up's current answer stays fully visible
          // until this lands. Only FRESH questions join the Recent list: a
          // follow-up fragment ("make it weekly") is meaningless re-asked
          // without its session and would resolve to garbage from the dropdown.
          entry = pushTrail({ question, payload: data });
          if (fresh) pushRecent(question);
          buildAnswerSkeleton(data);
          applyModelTooltip(data);
          chartCard = repaintChartAndTable(data);
          renderAnswerWaiting(answerBody);
          renderKeepFooter(data);
          renderFollowUp();
          hasAnswer = true;
          announce("Data ready — writing commentary");
        } else if (event === "done") {
          if (dismissed || seq !== requestSeq || !entry) return;
          setBusy(false);
          // Merge the commentary into the (partial) resolved payload so the
          // trail entry carries the full answer for add / follow-up context.
          const full = { ...entry.payload, ...data };
          entry.payload = full;
          if (
            chartCard &&
            Array.isArray(full.annotations) &&
            full.annotations.length
          ) {
            setChartAnnotations(chartCard, full.annotations);
          }
          renderAnswer(full, chartCard, seq, answerBody);
          renderKeepFooter(full);
          announce("Answer ready");
        } else if (event === "error") {
          if (dismissed || seq !== requestSeq) return;
          setBusy(false);
          const msg = data.detail || data.error || "Ask request failed";
          if (fresh || !slots) {
            renderError(msg);
          } else {
            // Non-destructive follow-up failure: keep the current answer.
            showFollowupBusy(false);
            showInlineError(msg);
          }
        }
      },
    });
  }

  async function submit(question, opts = {}) {
    // A fresh header ask starts a new session; a follow-up keeps the trail and
    // appends its answer on success.
    const fresh = !!opts.fresh;
    if (fresh) trail = [];
    dismissed = false; // a new submit re-activates the panel
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    setBusy(true);
    // A fresh ask clears to the loading shell; a follow-up keeps the current
    // answer fully visible and only shows a slim inline busy row + disables its
    // input (the `resolved` event then rebuilds the skeleton).
    if (fresh) renderLoading();
    else showFollowupBusy(true);

    // A follow-up failure never blows away the visible answer: surface it
    // through the inline error slot and re-enable the input (its text is
    // untouched, so edit-and-retry works).
    const fail = (msg) => {
      if (fresh) {
        renderError(msg);
      } else {
        showFollowupBusy(false);
        showInlineError(msg);
      }
    };
    const failNotice = (msg) => {
      if (fresh) {
        renderNotice(msg);
      } else {
        showFollowupBusy(false);
        showInlineError(msg);
      }
    };

    try {
      const reqBody = { question, params: gatherParams(), stream: true };
      if (opts.history && opts.history.length) reqBody.history = opts.history;
      const response = await postJson(_ASK_URL, reqBody, {
        signal: controller.signal,
      });
      if (dismissed || seq !== requestSeq) return; // closed / newer question took over
      // Staged SSE (the normal path) vs. plain JSON (a 429 / notice / disabled
      // / proxy fallback — checked before streaming).
      const contentType = response.headers.get("Content-Type") || "";
      if (response.ok && contentType.includes("text/event-stream")) {
        await consumeAskStream(response, question, seq, fresh);
        return;
      }
      const data = await response.json().catch(() => null);
      if (dismissed || seq !== requestSeq) return;
      setBusy(false);
      if (!data) {
        fail(`HTTP ${response.status}`);
        return;
      }
      if (data.notice) {
        failNotice(data.notice);
        return;
      }
      if (!response.ok || data.error) {
        fail(data.error || data.detail || `HTTP ${response.status}`);
        return;
      }
      // A non-streaming JSON answer (shouldn't happen on the happy path, but
      // stay robust): render it whole like the pre-staging client did.
      pushTrail({ question, payload: data });
      if (fresh) pushRecent(question);
      renderAnswerPayload(data, seq);
    } catch (error) {
      if (
        dismissed ||
        seq !== requestSeq ||
        (error && error.name === "AbortError")
      ) {
        return;
      }
      setBusy(false);
      console.error("dashdown ask box: request failed", error);
      fail((error && error.message) || "Ask failed");
    }
  }

  // site_search.js fires this when the operator picks the "Ask the data" row
  // (or hits Enter in ask-only mode). The modules stay decoupled — no import
  // either way, just the DOM event. A header ask always STARTS A NEW SESSION
  // (clears the trail, no history) — the follow-up field carries the trail.
  el.addEventListener("dashdown:ask", (ev) => {
    const q = ((ev.detail && ev.detail.question) || "").trim();
    if (q) submit(q, { fresh: true });
  });

  // The omnibox compose row ("✎ Add to this page" on an imperative-shaped
  // input) — straight to plan+preview, same decoupled DOM-event channel.
  el.addEventListener("dashdown:compose", (ev) => {
    const instruction = ((ev.detail && ev.detail.instruction) || "").trim();
    if (instruction) submitCompose(instruction);
  });

  // Escape closes the answer panel *first*, then falls through to search's own
  // Escape on a second press. Capture phase on `el` (an ancestor of the input)
  // fires before site_search.js's target-phase keydown, so stopPropagation
  // keeps the first Escape from also closing/blurring search. The follow-up
  // field has its own layering (its Escape steps focus back to the omnibox
  // input, then a second Escape lands here and closes).
  el.addEventListener(
    "keydown",
    (ev) => {
      if (ev.key !== "Escape" || panel.hidden) return;
      // A follow-up-field Escape handles its own step-out first; don't close.
      if (followupInput && document.activeElement === followupInput) return;
      ev.preventDefault();
      ev.stopPropagation();
      close();
    },
    true
  );

  // Typing a new query closes the answer and lets search take over (the search
  // dropdown re-appears once `dashdown-ask-answer-open` is dropped).
  input.addEventListener("input", () => {
    if (!panel.hidden) close();
  });

  // Reopening re-shows the last answer without re-asking. Focus AND click are
  // both wired: after Esc-close the input keeps focus, so a later click on the
  // still-focused field fires no `focus` event — the click handler covers it.
  // `open()` re-hides the search dropdown, so the two panels never stack.
  const reopen = () => {
    if (suppressReopen) return; // the ✕ handler's programmatic refocus
    if (hasAnswer) open();
  };
  input.addEventListener("focus", reopen);
  input.addEventListener("click", reopen);

  // Click-away closes the panel (leaves its content for the next reopen). While
  // expanded the panel lives in a modal <dialog> (outside `el`), so the dialog
  // owns dismissal — don't let an in-dialog click read as "outside" and close.
  document.addEventListener("click", (ev) => {
    if (expanded) return;
    if (!el.contains(ev.target)) close();
  });

  // A theme toggle re-bakes the panel chart's theme (ECharts applies a theme
  // only at init, and this chart isn't in chart.js's registry). Dispose +
  // re-init on the same container, then repaint the stored records.
  onThemeChange(() => {
    if (!chartState || !chartState.card) return;
    const card = chartState.card;
    const records = card._chartRecords;
    const old = card._echarts_instance;
    if (old) {
      try {
        old.dispose();
      } catch (e) {
        /* already disposed */
      }
    }
    const next = echarts.init(chartState.container, currentEChartsTheme());
    card._echarts_instance = next;
    if (card._chartInstance) card._chartInstance.echartsInstance = next;
    if (Array.isArray(records) && records.length) {
      // Re-run the same paint helper so the y-axis-name suppression is applied
      // on the freshly re-initialized instance too.
      paintPanelChart(card, records, chartState.config);
    }
  });

  // Deep-link prefill: ?_ask=<question> fills the omnibox, focuses + selects it,
  // and waits for the operator to press Enter — NEVER auto-submits (the
  // confirm-first cost guard). parseUrlParams() strips _-prefixed keys, so read
  // the raw URL; nothing is stripped from the URL.
  try {
    const askParam = new URLSearchParams(window.location.search).get("_ask");
    if (askParam) {
      input.value = askParam;
      // Defer so this wins over other init-time focus handling; a programmatic
      // value set fires no `input` event, so the answer panel isn't disturbed.
      setTimeout(() => {
        input.focus();
        input.select();
      }, 0);
    }
  } catch (e) {
    /* malformed URL — skip the prefill */
  }
}

/**
 * Wire the ask surface onto every omnibox on the page whose config opts in
 * (`ask: true`). A no-op on search-only boxes and when ask is off entirely (the
 * server then emits no ask flag), so static builds / embeds cost nothing.
 */
export function initAskIntegration() {
  document
    .querySelectorAll('[data-async-component="site-search"]')
    .forEach((el) => initOne(el));
}
