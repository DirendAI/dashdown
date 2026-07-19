// Dashdown Ask Integration (omnibox answer panel)
//
// The runtime operator "ask" surface, merged into the centered site-search box
// (site_search.js). There is no standalone ask box any more: the search input
// doubles as the ask input, and this module attaches an answer panel *under the
// same box*. Flow: the user picks the "Ask the data" row (or, in ask-only mode,
// just hits Enter); site_search.js fires a `dashdown:ask` DOM event; this module
// runs the submit — POST /_dashdown/api/ask, which resolves the question against
// the project's semantic models / named queries and returns a single JSON
// payload — then paints provenance + an auto-inferred chart + a result table +
// a typewriter answer (answer-first order preserved).
//
// This is the free-form sibling of the authored <Ask /> card (ask.js): it reuses
// the same typewriter feel, the same chart-annotation ref chips, and the same
// chart/table renderers, but the question is the operator's, not the author's.
//
// Answer refinement (semantic answers only): the static provenance line becomes
// an *interactive chip row* — metric / by / grain selects, removable filter
// chips, and a "+ filter" popover. Editing a chip is treated as editing a query
// (not a chat): it POSTs to /_dashdown/api/ask/execute with the edited spec and
// `commentary:false`, repaints the chart + table, and marks the answer prose
// *stale* (dimmed, with an "↻ Update commentary" button) rather than re-writing
// it on every twiddle. A slim follow-up input at the panel bottom re-asks with
// the whole session trail as context (`history`, oldest-first — the server keeps
// the last few), and echoes the new question into the header omnibox so a
// re-Enter re-asks it. The trail also drives a pill row at the panel top: each
// ask/follow-up is a pill, and clicking an older one restores that answer
// client-side (the payloads are held in the trail, so no server round-trip).
//
// Ask is gated server-side (the box's data-config `ask` flag comes from
// `ask_enabled` — llm on ∧ ask on ∧ not embed), so this never wires up in static
// builds or embeds. The panel is only built on user interaction, so a headless
// print/screenshot run (which never asks) is untouched.
//
// The "Keep on this page" button (when `ask_keep` is on and the answer resolved
// to a semantic metric or named query) POSTs to /_dashdown/api/ask/keep to append
// the answer's chart to the current page's source markdown; the dev server's file
// watcher then live-reloads the page. It keeps the *current edited* spec — the
// keep footer always reads the latest /ask or /ask/execute payload.

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
const _EXECUTE_URL = "/_dashdown/api/ask/execute";
const _KEEP_URL = "/_dashdown/api/ask/keep";

// localStorage key of the operator's recent questions (newest-first), read by
// site_search.js's empty-focus dropdown; sessionStorage key prefix of the
// persisted, payload-light session (keyed per path, below).
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
 * under the box, and listens for the `dashdown:ask` event site_search.js fires.
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
  // build it and anchor it under the same box. It reuses the ask-box panel
  // chrome; a scoping class widens it to the search slot (see dashdown.css).
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

  const MAX_TRAIL = 6; // cap the session trail; drop-oldest beyond this

  let requestSeq = 0; // drop responses a newer question/execute superseded
  let abortController = null;
  let dismissed = false; // set by close(); guards late async continuations from
  // re-opening the panel (a response landing after close never auto-opens). A
  // new submit/execute clears it.
  let expanded = false; // panel promoted into the expand <dialog>
  let expandDialog = null; // the live <dialog> while expanded, else null
  let hasAnswer = false; // panel holds a rendered answer (for reopen)
  // Session trail: the ask → follow-up chain, oldest-first. Each entry is
  // `{question, payload}` where payload is the full /ask (or /ask/execute)
  // response the operator is looking at. A fresh header ask starts a new
  // session (clears the trail); a follow-up appends; a chip edit / ↻ commentary
  // updates the CURRENT (last) entry's payload in place; a trail-pill click
  // restores an older entry (truncating everything after it). All the
  // "current answer" reads (keep, refinement, follow-up context) go through the
  // last entry via currentEntry/currentPayload/currentQuestion.
  let trail = []; // [{question, payload}]
  // The live panel chart, so a theme toggle can dispose + re-init it (it's not
  // in chart.js's registry, so onThemeChange there won't reach it).
  let chartState = null; // { card, container, config }

  // Persist-light session (4A-b): a per-path sessionStorage snapshot of the
  // trail carrying ONLY question + resolved {kind, detail} + chart (never rows /
  // columns / answer text — quota safety; the trail cap already bounds it). On
  // reload it powers site_search.js's "Continue" row via el.dataset.askResume; a
  // restored ask re-asks through the normal path and rebuilds a FRESH session —
  // v1 does NOT reconstruct trail pills from these payload-less entries.
  const _SESSION_KEY = "dashdown-ask-session:" + window.location.pathname;

  function persistSession() {
    try {
      const entries = trail.map((e) => ({
        question: e.question,
        resolved: {
          kind: e.payload && e.payload.resolved ? e.payload.resolved.kind : undefined,
          detail: e.payload && e.payload.resolved ? e.payload.resolved.detail : undefined,
        },
        chart: (e.payload && e.payload.chart) || null,
      }));
      window.sessionStorage.setItem(
        _SESSION_KEY,
        JSON.stringify({ v: 1, entries })
      );
    } catch (e) {
      /* private mode / quota — the session snapshot is best-effort */
    }
  }

  // Expose (or clear) the restored session's last question to the empty-focus
  // dropdown via a data attribute site_search.js reads (a decoupled channel — no
  // import either way). Cleared once a live answer exists (the answer panel
  // reopens on focus, so a "Continue" row would be redundant). site_search fires
  // "dashdown:ask-resume" before rendering that dropdown to pull a fresh value.
  function refreshResume() {
    if (hasAnswer) {
      delete el.dataset.askResume;
      return;
    }
    try {
      const raw = window.sessionStorage.getItem(_SESSION_KEY);
      const stored = raw ? JSON.parse(raw) : null;
      const entries = stored && Array.isArray(stored.entries) ? stored.entries : [];
      const last = entries.length ? entries[entries.length - 1] : null;
      const q = last && typeof last.question === "string" ? last.question.trim() : "";
      if (q) el.dataset.askResume = q;
      else delete el.dataset.askResume;
    } catch (e) {
      delete el.dataset.askResume;
    }
  }

  const currentEntry = () => (trail.length ? trail[trail.length - 1] : null);
  const currentPayload = () => {
    const e = currentEntry();
    return e ? e.payload : null;
  };
  const currentQuestion = () => {
    const e = currentEntry();
    return e ? e.question : "";
  };
  const updateCurrentPayload = (payload) => {
    if (trail.length) trail[trail.length - 1].payload = payload;
    persistSession();
  };

  // Rebuilt on each answer render (buildAnswerSkeleton); the refinement paths
  // target these stable slots instead of the whole panel.
  let slots = null; // { trail, prov, err, bodyWrap, chart, table, keep, followup }
  let answerBody = null; // the .dashdown-ask-body that holds the typed prose
  let updateBtn = null; // "↻ Update commentary" (revealed when prose is stale)
  let followupInput = null; // the bottom "refine or follow-up" field
  let followupBusyRow = null; // slim inline busy row while a follow-up loads
  let chipDebounceTimer = null; // coalesces a burst of chip edits (see 15A)

  // Semantic-refinement state. `chipState` is the editable spec the chip row
  // builds from; `semanticOptions` is the payload's option lists (measures,
  // dimensions, grains, the time dimension). Both are reset by renderChips.
  let chipState = null;
  let semanticOptions = null;

  function setBusy(busy) {
    el.classList.toggle("dashdown-ask-answer-busy", busy);
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
    // check `dismissed`). A new submit/execute clears the flag.
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
    persistSession();
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
    updateBtn = null;
    followupInput = null;
    followupBusyRow = null;
  }

  // Build a shareable deep link to the current question: this page's URL with a
  // `?_ask=<question>` param added (existing non-_ask params preserved). Opening
  // it prefills the omnibox but never auto-submits (see the init handler).
  function copyLinkUrl() {
    const params = new URLSearchParams(window.location.search);
    params.set("_ask", currentQuestion());
    const qs = params.toString();
    return (
      window.location.origin +
      window.location.pathname +
      (qs ? "?" + qs : "")
    );
  }

  // Copy the deep link to the clipboard with brief "✓" feedback on the button.
  function onCopyLink() {
    const btn = panel.querySelector(".dashdown-ask-box-copy");
    const flashDone = () => {
      if (!btn) return;
      btn.textContent = "✓";
      btn.classList.add("dashdown-ask-box-copy-done");
      setTimeout(() => {
        btn.textContent = "🔗";
        btn.classList.remove("dashdown-ask-box-copy-done");
      }, 1500);
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(copyLinkUrl()).then(flashDone, () => {});
      }
    } catch (e) {
      /* clipboard unavailable — no-op */
    }
  }

  // Topbar (badge + copy + expand + close) shared by the transient states and
  // the full answer. buildTopbar reads `expanded` so a rebuild while expanded
  // keeps the Collapse affordance. The copy-link button is offered only once a
  // question exists (the transient loading shell has an empty trail).
  function buildTopbar() {
    const header = document.createElement("div");
    header.className = "dashdown-ask-box-topbar";
    const expandGlyph = expanded ? "⤡" : "⤢";
    const expandLabel = expanded ? "Collapse answer" : "Expand answer";
    const showCopy = !!currentQuestion();
    header.innerHTML =
      _AI_BADGE +
      '<span class="dashdown-ask-box-topbar-actions">' +
      (showCopy
        ? '<button type="button" class="dashdown-ask-box-copy" aria-label="Copy link to this question">🔗</button>'
        : "") +
      `<button type="button" class="dashdown-ask-box-expand" aria-label="${expandLabel}">${expandGlyph}</button>` +
      '<button type="button" class="dashdown-ask-box-close" aria-label="Close answer">✕</button>' +
      "</span>";
    const copyBtn = header.querySelector(".dashdown-ask-box-copy");
    if (copyBtn) copyBtn.addEventListener("click", onCopyLink);
    header
      .querySelector(".dashdown-ask-box-expand")
      .addEventListener("click", toggleExpand);
    header
      .querySelector(".dashdown-ask-box-close")
      .addEventListener("click", () => {
        close();
        input.focus();
      });
    return header;
  }

  // Minimal shell for transient states (loading / error / notice): topbar only.
  function panelShell() {
    resetPanel();
    panel.appendChild(buildTopbar());
    open();
    return panel;
  }

  // Full answer skeleton: topbar + the stable slots the refinement paths target.
  function buildAnswerSkeleton() {
    resetPanel();
    panel.appendChild(buildTopbar());

    slots = {};
    slots.trail = mkDiv("dashdown-ask-box-trail");
    slots.trail.hidden = true;
    slots.prov = mkDiv("dashdown-ask-box-prov");
    slots.err = mkDiv("dashdown-ask-error dashdown-ask-box-message dashdown-ask-box-inline-error");
    slots.err.hidden = true;

    slots.bodyWrap = mkDiv("dashdown-ask-box-body-wrap");
    updateBtn = document.createElement("button");
    updateBtn.type = "button";
    updateBtn.className = "dashdown-ask-box-update";
    updateBtn.hidden = true;
    updateBtn.textContent = "↻ Update commentary";
    updateBtn.addEventListener("click", onUpdateCommentary);
    answerBody = mkDiv("dashdown-ask-body dashdown-ask-box-body");
    slots.bodyWrap.appendChild(updateBtn);
    slots.bodyWrap.appendChild(answerBody);

    slots.chart = mkDiv("dashdown-ask-box-chart-slot");
    slots.table = mkDiv("dashdown-ask-box-table-slot");
    slots.keep = mkDiv("dashdown-ask-box-keep-slot");
    slots.followup = mkDiv("dashdown-ask-box-followup-slot");

    panel.appendChild(slots.trail);
    panel.appendChild(slots.prov);
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

  function renderError(message) {
    panelShell();
    const div = document.createElement("div");
    div.className = "dashdown-ask-error dashdown-ask-box-message";
    div.textContent = message || "Ask request failed";
    panel.appendChild(div);
    announce("Ask failed: " + (message || "Ask request failed"));
    hasAnswer = true;
  }

  function renderNotice(message) {
    panelShell();
    const div = document.createElement("div");
    div.className = "dashdown-ask-notice dashdown-ask-box-message";
    div.textContent = message || "Ask is unavailable";
    panel.appendChild(div);
    announce(message || "Ask is unavailable");
    hasAnswer = true;
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

  // Inline (non-destructive) error for the refinement paths — a 429/rate-limit
  // or an invalid-spec 400 shows here without blowing away the answer.
  function showExecError(message) {
    if (!slots || !slots.err) return;
    slots.err.textContent = message || "Refine request failed";
    slots.err.hidden = false;
    announce("Ask failed: " + (message || "Refine request failed"));
  }
  function clearExecError() {
    if (!slots || !slots.err) return;
    slots.err.textContent = "";
    slots.err.hidden = true;
  }

  // Paint (or repaint) the panel chart, then suppress the y-axis name ECharts
  // draws above the axis: the compact panel has no headroom for it (it clips
  // against the provenance line) and the provenance + table header already name
  // the metric. This is the single place the suppression lives, so renderChart,
  // the repaint() shim, and the theme-change re-init can't drift out of sync
  // (a theme toggle re-inits the chart, which would otherwise bring it back).
  function paintPanelChart(card, records, config) {
    updateChart(card, records, config);
    if (card._echarts_instance) {
      card._echarts_instance.setOption({ yAxis: { name: "" } });
    }
  }

  // Build a chart host that speaks the same _chartConfig/_echarts_instance/
  // _chartInstance contract the annotation helpers expect, so setChartAnnotations
  // + emphasizeChartAnnotation work unchanged against it. Appended to `parent`
  // (a stable chart slot), so a repaint can clear the slot and rebuild.
  function renderChart(payload, parent) {
    const records = recordsOf(payload);
    const spec = payload.chart || {};
    const config = {
      type: spec.type,
      x: spec.x,
      y: spec.y,
      title: spec.title || "",
    };
    // The server ships a concrete chart type, which skips chart.js's
    // resolveAutoConfig — where an auto-chart would derive sort_by for a temporal
    // x. So thread the server's sort hint through (temporal charts set sort_by=x)
    // or a time series renders in row order instead of by time.
    if (spec.sort_by) config.sort_by = spec.sort_by;
    // A series-split answer (by + series, e.g. "revenue by week per channel")
    // carries the splitting column — chart.js's series_by config key.
    if (spec.series_by) config.series_by = spec.series_by;
    const card = document.createElement("div");
    card.className = "dashdown-chart dashdown-ask-box-chart";
    card.innerHTML = '<div class="dashdown-chart-container dashdown-ask-box-chart-container"></div>';
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

  // Repaint a mounted chart in place from a fresh payload of the SAME type: keep
  // the ECharts instance (no dispose+init flicker) and drive it via setOption,
  // updating the config fields the panel threads. Returns the existing card.
  function reuseChart(payload) {
    const card = chartState.card;
    const config = card._chartConfig;
    const spec = payload.chart || {};
    // type is unchanged (that's the reuse gate) — refresh the rest in place.
    config.x = spec.x;
    config.y = spec.y;
    config.title = spec.title || "";
    if (spec.sort_by) config.sort_by = spec.sort_by;
    else delete config.sort_by;
    if (spec.series_by) config.series_by = spec.series_by;
    else delete config.series_by;
    paintPanelChart(card, recordsOf(payload), config);
    // Re-apply (or clear) annotations for the fresh payload; setChartAnnotations
    // with [] clears any stale marks left from the prior spec.
    const anns = Array.isArray(payload.annotations) ? payload.annotations : [];
    const hadAnns =
      Array.isArray(config.annotations) && config.annotations.length;
    if (anns.length || hadAnns) setChartAnnotations(card, anns);
    return card;
  }

  // Rebuild the chart + table slots from a payload. Returns the chart card (or
  // null when the payload carries no chart / no data) so the answer's annotation
  // ref chips can wire against it. Shared by the first render and every refine.
  // A same-type repaint reuses the mounted ECharts instance (setOption); only a
  // type change / no-chart payload disposes and rebuilds.
  function repaintChartAndTable(payload) {
    slots.table.innerHTML = "";
    const spec = payload.chart || {};
    const hasData = payload.columns && payload.rows && payload.rows.length;
    const canReuse =
      chartState &&
      chartState.card &&
      chartState.card._echarts_instance &&
      payload.chart &&
      hasData &&
      chartState.config &&
      chartState.config.type === spec.type;

    let chartCard = null;
    if (canReuse) {
      try {
        chartCard = reuseChart(payload);
      } catch (e) {
        console.error("dashdown ask box: chart repaint failed", e);
        disposeChart();
        slots.chart.innerHTML = "";
        chartCard = null;
      }
    } else {
      disposeChart();
      slots.chart.innerHTML = "";
      if (payload.chart && hasData) {
        try {
          chartCard = renderChart(payload, slots.chart);
        } catch (e) {
          console.error("dashdown ask box: chart render failed", e);
          chartCard = null;
        }
      }
    }
    if (hasData) {
      try {
        renderTable(payload, slots.table);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
      // A capped result set flags how much was withheld (server: `truncated` +
      // `total_rows`). Re-rendered with each repaint (the table slot is cleared
      // above), so a refine that returns a full set drops the note.
      if (payload.truncated) {
        const foot = mkDiv("dashdown-ask-box-truncated");
        const shown = (payload.rows && payload.rows.length) || 0;
        const total = Number(payload.total_rows);
        foot.textContent =
          `Showing first ${shown.toLocaleString()} of ` +
          `${(isFinite(total) ? total : shown).toLocaleString()} rows`;
        slots.table.appendChild(foot);
      }
    }
    return chartCard;
  }

  // Type the answer out (the shared ask.js typewriter cadence) into `bodyEl`,
  // then swap in the sanitized answer_html and wire its ref chips. `skipTypewriter`
  // (restoring a stored answer from the trail) short-circuits to the final HTML;
  // typewriterInto itself handles the reduced-motion / no-words fast path.
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

  // ---- Semantic refinement: the interactive provenance chip row -----------

  // Provenance region: the interactive chip row for a semantic answer (when the
  // payload carries `semantic_options`), else the static provenance text a query
  // answer keeps as before.
  function renderProvenance(payload) {
    if (!slots) return;
    slots.prov.innerHTML = "";
    const resolved = payload.resolved || {};
    if (resolved.kind === "semantic" && payload.semantic_options) {
      renderChips(payload);
    } else if (resolved.provenance && resolved.kind !== "none") {
      // An unresolved answer's provenance is "unresolved: <reason>" — the exact
      // text the body already shows. Skip the redundant line.
      const prov = document.createElement("div");
      prov.className = "dashdown-ask-box-provenance";
      prov.textContent = resolved.provenance;
      slots.prov.appendChild(prov);
    }
  }

  // A native <select> styled chip-like (keyboard/a11y for free), captioned with
  // the field it edits. `options` is [{value, text}]; `onChange(value)` fires.
  function makeChipSelect(label, options, value, onChange) {
    const wrap = document.createElement("label");
    wrap.className = "dashdown-ask-chip dashdown-ask-chip-select";
    const cap = document.createElement("span");
    cap.className = "dashdown-ask-chip-label";
    cap.textContent = label;
    const sel = document.createElement("select");
    sel.className = "dashdown-ask-chip-control";
    sel.setAttribute("aria-label", label);
    for (const opt of options) {
      const o = document.createElement("option");
      o.value = opt.value;
      o.textContent = opt.text;
      if (opt.value === value) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => onChange(sel.value));
    wrap.appendChild(cap);
    wrap.appendChild(sel);
    return wrap;
  }

  // A removable filter chip: "dim: v1, v2 ×".
  function makeFilterChip(dim, values) {
    const chip = document.createElement("span");
    chip.className = "dashdown-ask-chip dashdown-ask-chip-filter";
    const txt = document.createElement("span");
    txt.className = "dashdown-ask-chip-filter-text";
    txt.textContent = `${dim}: ${values.join(", ")}`;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "dashdown-ask-chip-remove";
    x.setAttribute("aria-label", `Remove filter ${dim}`);
    x.textContent = "×";
    x.addEventListener("click", () => {
      delete chipState.filters[dim];
      scheduleChipChange();
    });
    chip.appendChild(txt);
    chip.appendChild(x);
    return chip;
  }

  // The "+ filter" chip: a button toggling a small popover with a dimension
  // select, a comma-separated value input, and Add.
  function makeAddFilterChip(dims) {
    const wrap = document.createElement("span");
    wrap.className = "dashdown-ask-chip dashdown-ask-chip-add";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dashdown-ask-chip-add-btn";
    btn.textContent = "+ filter";
    btn.setAttribute("aria-haspopup", "true");
    btn.setAttribute("aria-expanded", "false");

    const pop = mkDiv("dashdown-ask-filter-pop");
    pop.hidden = true;
    const sel = document.createElement("select");
    sel.className = "dashdown-ask-filter-pop-dim";
    sel.setAttribute("aria-label", "Filter dimension");
    for (const d of dims) {
      const o = document.createElement("option");
      o.value = d;
      o.textContent = d;
      sel.appendChild(o);
    }
    const val = document.createElement("input");
    val.type = "text";
    val.className = "dashdown-ask-filter-pop-val";
    val.placeholder = "value, value…";
    val.setAttribute("aria-label", "Filter values (comma-separated)");
    const add = document.createElement("button");
    add.type = "button";
    add.className = "dashdown-ask-filter-pop-add";
    add.textContent = "Add";

    const closePop = () => {
      pop.hidden = true;
      btn.setAttribute("aria-expanded", "false");
    };
    const applyAdd = () => {
      const dim = sel.value;
      const values = val.value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      closePop();
      if (!dim || !values.length) return;
      chipState.filters[dim] = values;
      scheduleChipChange();
    };

    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const show = pop.hidden;
      pop.hidden = !show;
      btn.setAttribute("aria-expanded", String(show));
      if (show) val.focus();
    });
    add.addEventListener("click", applyAdd);
    val.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        applyAdd();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.stopPropagation();
        closePop();
        btn.focus();
      }
    });
    // Clicks inside the popover stay inside it (don't reach the panel-close
    // document handler, and don't count as an outside click).
    pop.addEventListener("click", (ev) => ev.stopPropagation());

    pop.appendChild(sel);
    pop.appendChild(val);
    pop.appendChild(add);
    wrap.appendChild(btn);
    wrap.appendChild(pop);
    return wrap;
  }

  // Build the chip row from a semantic payload and seed `chipState` from its
  // resolution detail. Also keeps the muted provenance text line beneath the
  // chips (the trust surface) so the provenance node is always present.
  function renderChips(payload) {
    const opts = payload.semantic_options || {};
    const detail = (payload.resolved && payload.resolved.detail) || {};
    semanticOptions = opts;
    chipState = {
      model: opts.model || detail.model || "",
      metric: detail.metric || "",
      by: detail.by || null,
      series: detail.series || null,
      grain: detail.grain || null,
      chart: detail.chart || "",
      filters: {},
    };
    const rawFilters = detail.filters || {};
    for (const k of Object.keys(rawFilters)) {
      const v = rawFilters[k];
      chipState.filters[k] = Array.isArray(v) ? v.slice() : [String(v)];
    }

    const row = mkDiv("dashdown-ask-chips");
    const spark = document.createElement("span");
    spark.className = "dashdown-ask-chips-spark";
    spark.textContent = "✦";
    spark.setAttribute("aria-hidden", "true");
    row.appendChild(spark);

    // metric — one of the model's measures.
    const measures = opts.measures || [];
    row.appendChild(
      makeChipSelect(
        "metric",
        measures.map((m) => ({ value: m, text: m })),
        chipState.metric,
        (v) => {
          chipState.metric = v;
          scheduleChipChange();
        }
      )
    );

    // by — a dimension, or "—" for none.
    const dims = opts.dimensions || [];
    const byOptions = [{ value: "", text: "—" }].concat(
      dims.map((d) => ({ value: d, text: d }))
    );
    row.appendChild(
      makeChipSelect("by", byOptions, chipState.by || "", (v) => {
        chipState.by = v || null;
        scheduleChipChange();
      })
    );

    // series — a second grouping dimension ("per channel"), splitting the
    // metric into one colored series per value. Only offered once a primary
    // `by` exists, and never the same dimension as `by`.
    if (chipState.by) {
      const seriesOptions = [{ value: "", text: "—" }].concat(
        dims
          .filter((d) => d !== chipState.by)
          .map((d) => ({ value: d, text: d }))
      );
      row.appendChild(
        makeChipSelect("per", seriesOptions, chipState.series || "", (v) => {
          chipState.series = v || null;
          scheduleChipChange();
        })
      );
    }

    // grain — only meaningful (and only rendered) when `by` is the time
    // dimension. A grain on a categorical dimension is meaningless.
    if (
      chipState.by &&
      opts.time_dimension &&
      chipState.by === opts.time_dimension
    ) {
      const grains = opts.grains || [];
      if (grains.length) {
        row.appendChild(
          makeChipSelect(
            "grain",
            grains.map((g) => ({ value: g, text: g })),
            chipState.grain || grains[0],
            (v) => {
              chipState.grain = v || null;
              scheduleChipChange();
            }
          )
        );
      }
    }

    // chart — the presentation wish ("as a funnel"). "auto" clears it and the
    // server re-infers; an incompatible wish is soft-dropped server-side.
    const chartTypes = ["line", "bar", "scatter", "pie", "funnel", "treemap"];
    row.appendChild(
      makeChipSelect(
        "chart",
        [{ value: "", text: "auto" }].concat(
          chartTypes.map((t) => ({ value: t, text: t }))
        ),
        chipState.chart || "",
        (v) => {
          chipState.chart = v || "";
          scheduleChipChange();
        }
      )
    );

    // Existing filters as removable chips.
    for (const dim of Object.keys(chipState.filters)) {
      row.appendChild(makeFilterChip(dim, chipState.filters[dim]));
    }

    // + filter.
    row.appendChild(makeAddFilterChip(dims));

    slots.prov.innerHTML = "";
    slots.prov.appendChild(row);

    const resolved = payload.resolved || {};
    if (resolved.provenance) {
      const prov = document.createElement("div");
      prov.className =
        "dashdown-ask-box-provenance dashdown-ask-chips-provenance";
      prov.textContent = resolved.provenance;
      slots.prov.appendChild(prov);
    }
  }

  // Build the /ask/execute spec from the live chip state. Grain rides only when
  // `by` is the time dimension (mirrors the render gate).
  function buildSpecFromChips() {
    const byIsTime =
      chipState.by &&
      semanticOptions &&
      chipState.by === semanticOptions.time_dimension;
    return {
      kind: "semantic",
      model: chipState.model,
      metric: chipState.metric,
      by: chipState.by || null,
      // A series without a primary grouping is meaningless — dropped with by.
      series: chipState.by ? chipState.series || null : null,
      grain: byIsTime ? chipState.grain || null : null,
      chart: chipState.chart || null,
      filters: chipState.filters || {},
    };
  }

  function markCommentaryStale() {
    if (answerBody) answerBody.classList.add("dashdown-ask-box-body-stale");
    if (updateBtn) updateBtn.hidden = false;
  }
  function unmarkCommentaryStale() {
    if (answerBody) answerBody.classList.remove("dashdown-ask-box-body-stale");
    if (updateBtn) updateBtn.hidden = true;
  }

  // POST the edited spec to /ask/execute. Reuses the requestSeq/abort pattern so
  // a stale execute response never paints over a newer one. Returns
  // {data, seq} on success, {error, seq} on a handled failure (400/429/notice),
  // or null when superseded/aborted (caller no-ops).
  async function executeSpec(spec, commentary) {
    dismissed = false; // a fresh execute re-activates the panel
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    setBusy(true);
    try {
      const response = await postJson(
        _EXECUTE_URL,
        {
          question: currentQuestion(),
          spec,
          params: gatherParams(),
          commentary: !!commentary,
          refresh: false,
        },
        { signal: controller.signal }
      );
      if (dismissed || seq !== requestSeq) return null;
      const data = await response.json().catch(() => null);
      if (dismissed || seq !== requestSeq) return null;
      setBusy(false);
      if (!data) return { error: `HTTP ${response.status}`, seq };
      if (data.notice) return { error: data.notice, seq };
      if (!response.ok || data.error) {
        return {
          error: data.error || data.detail || `HTTP ${response.status}`,
          seq,
        };
      }
      return { data, seq };
    } catch (error) {
      if (dismissed || seq !== requestSeq || (error && error.name === "AbortError")) {
        return null;
      }
      setBusy(false);
      return { error: (error && error.message) || "Refine request failed", seq };
    }
  }

  // Debounce a burst of chip twiddles (select changes + filter add/remove all
  // share this one timer) so a rapid sequence coalesces into a single execute.
  // The client abort in executeSpec bounds only the *paint* — a superseded
  // response is dropped, but its server-side query may already be running — so
  // coalescing here is what actually avoids billing an execute per keystroke.
  const _CHIP_DEBOUNCE_MS = 250;
  function scheduleChipChange() {
    clearTimeout(chipDebounceTimer);
    chipDebounceTimer = setTimeout(() => {
      chipDebounceTimer = null;
      onChipChange();
    }, _CHIP_DEBOUNCE_MS);
  }

  // A chip edit: re-run the spec with commentary OFF (fast, cheap), repaint the
  // chart + table + chips, and mark the prose stale rather than re-writing it.
  async function onChipChange() {
    clearExecError();
    const spec = buildSpecFromChips();
    const res = await executeSpec(spec, false);
    if (!res) return; // superseded / aborted
    if (res.error) {
      showExecError(res.error);
      return;
    }
    // A chip edit updates what the operator sees, so it updates the CURRENT
    // trail entry's payload in place (the next follow-up's history reflects it).
    updateCurrentPayload(res.data);
    repaintChartAndTable(res.data);
    renderChips(res.data); // repaint chips + provenance from the response
    renderKeepFooter(res.data); // reset keep to the current edited spec
    markCommentaryStale();
  }

  // "↻ Update commentary": re-run the current chip spec with commentary ON, then
  // typewriter the fresh answer in and un-dim.
  async function onUpdateCommentary() {
    clearExecError();
    if (updateBtn) updateBtn.disabled = true;
    const spec = buildSpecFromChips();
    const res = await executeSpec(spec, true);
    if (updateBtn) updateBtn.disabled = false;
    if (!res) return;
    if (res.error) {
      showExecError(res.error);
      return;
    }
    updateCurrentPayload(res.data);
    const chartCard = repaintChartAndTable(res.data);
    renderChips(res.data);
    renderKeepFooter(res.data);
    renderAnswer(res.data, chartCard, res.seq, answerBody);
    unmarkCommentaryStale();
  }

  // ---- Keep + follow-up ---------------------------------------------------

  // "Keep on this page": append this answer's chart to the current page's source
  // markdown, so the operator's ad-hoc question becomes a permanent card. For
  // answers that resolved to a semantic metric, a named query, or a semantic
  // list (kept as an authored <List>) — a raw-SQL answer has no stable,
  // re-runnable reference to embed. Gated by the box's `ask_keep` config flag
  // (server: `ask_keep_enabled`). Rebuilt on every render/refine so it always
  // keeps the *current* edited spec (it posts the current trail entry's
  // `resolved`, which the server re-validates).
  const _KEEPABLE_KINDS = ["semantic", "query", "list"];

  function renderKeepFooter(payload) {
    slots.keep.innerHTML = "";
    if (!config.ask_keep) return;
    const resolved = payload.resolved || {};
    if (!_KEEPABLE_KINDS.includes(resolved.kind)) return;

    const footer = mkDiv("dashdown-ask-box-keep");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dashdown-ask-box-keep-btn";
    btn.textContent = "Keep on this page";
    const err = document.createElement("span");
    err.className = "dashdown-ask-box-keep-error";
    footer.appendChild(btn);
    footer.appendChild(err);
    slots.keep.appendChild(footer);

    btn.addEventListener("click", async () => {
      btn.disabled = true;
      err.textContent = "";
      try {
        // Keep the CURRENT edited answer: the last trail entry's payload is the
        // latest /ask or /ask/execute response, so chip edits are reflected.
        const current = currentPayload() || payload;
        const resp = await postJson(_KEEP_URL, {
          question: currentQuestion(),
          resolved: current.resolved,
          chart: current.chart,
          path: window.location.pathname,
        });
        const data = await resp.json().catch(() => null);
        if (resp.ok && data && data.ok) {
          // Success: the server appended the card; the dev server's watcher will
          // live-reload the page shortly. Don't reload programmatically.
          // Stash the new section's id so page_edit.js can flash it once the
          // reloaded page comes back up (best-effort — storage may be blocked).
          if (data.id) {
            try {
              window.sessionStorage.setItem("dashdown-keep-flash", data.id);
            } catch (e) {
              /* storage blocked — skip the flash */
            }
          }
          btn.textContent = "Kept ✓ — added below";
          btn.classList.add("dashdown-ask-box-keep-done");
        } else {
          btn.disabled = false;
          err.textContent =
            (data && data.detail) || `Keep failed (HTTP ${resp.status})`;
        }
      } catch (e) {
        btn.disabled = false;
        err.textContent = (e && e.message) || "Keep failed";
      }
    });
  }

  // A slim follow-up field at the panel bottom. Enter re-asks via the normal
  // submit flow, threading the whole session trail as `history` context (the
  // server keeps the last few), and echoes the new question into the header
  // omnibox so a re-Enter re-asks it.
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
        // last 6). Chip edits already updated the last entry's payload, so this
        // reflects exactly what the operator is looking at.
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
        // through to the dialog's native cancel/close (which collapses the
        // panel) — don't preventDefault it or steal focus to the omnibox that
        // sits outside the modal.
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

  function applyModelTooltip(payload) {
    // Model attribution on the ✦ badge tooltip — the same trust affordance the
    // authored ask cards carry (theirs hover-reveals a .dashdown-ask-model).
    if (payload.model) {
      const badge = panel.querySelector(".dashdown-ask-box-badge");
      if (badge) {
        badge.setAttribute("title", `AI-generated answer · ${payload.model}`);
      }
    }
  }

  // ---- Session trail pills ------------------------------------------------

  // A slim pill row at the top of the panel: one pill per trail entry (the
  // question, truncated ~40 chars), separated by "→". The current (last) pill is
  // emphasized and inert; older pills are buttons that restore that answer. Only
  // shown when the trail has ≥2 entries.
  function renderTrailPills() {
    if (!slots || !slots.trail) return;
    slots.trail.innerHTML = "";
    if (trail.length < 2) {
      slots.trail.hidden = true;
      return;
    }
    slots.trail.hidden = false;
    const row = mkDiv("dashdown-ask-trail");
    trail.forEach((entry, idx) => {
      if (idx > 0) {
        const sep = document.createElement("span");
        sep.className = "dashdown-ask-trail-sep";
        sep.textContent = "→";
        sep.setAttribute("aria-hidden", "true");
        row.appendChild(sep);
      }
      const full = entry.question || "";
      const short = full.length > 40 ? `${full.slice(0, 39)}…` : full;
      const isCurrent = idx === trail.length - 1;
      if (isCurrent) {
        const pill = document.createElement("span");
        pill.className =
          "dashdown-ask-trail-pill dashdown-ask-trail-pill-active";
        pill.textContent = short;
        pill.title = full;
        pill.setAttribute("aria-current", "true");
        row.appendChild(pill);
      } else {
        const pill = document.createElement("button");
        pill.type = "button";
        pill.className = "dashdown-ask-trail-pill";
        pill.textContent = short;
        pill.title = full;
        pill.addEventListener("click", (ev) => {
          // The restore rebuilds the panel synchronously, detaching this pill;
          // stop the click bubbling so the document click-away handler doesn't
          // then see the detached target as "outside" and close the panel.
          ev.stopPropagation();
          restoreTrailEntry(idx);
        });
        row.appendChild(pill);
      }
    });
    slots.trail.appendChild(row);
  }

  // Clicking an older trail pill restores that answer entirely client-side (the
  // payload is in hand — no server call): truncate the trail after it, then
  // repaint the panel from the stored payload (typewriter skipped — the answer
  // is already known) and write its question back into the omnibox.
  function restoreTrailEntry(idx) {
    if (idx < 0 || idx >= trail.length) return;
    trail = trail.slice(0, idx + 1);
    persistSession();
    const entry = currentEntry();
    if (!entry) return;
    input.value = entry.question;
    // Bump the seq so any in-flight typewriter/response can't paint over this.
    renderAnswerPayload(entry.payload, ++requestSeq, { skipTypewriter: true });
  }

  function renderAnswerPayload(payload, seq, opts = {}) {
    buildAnswerSkeleton();
    applyModelTooltip(payload);

    // Answer-first hierarchy: the operator asked a question, so the answer text
    // is the headline (it types in above the evidence). renderTrailPills shows
    // the session chain; renderProvenance builds the interactive chip row
    // (semantic) or the static provenance line; the chart must render before
    // renderAnswer so the answer's annotation ref chips have a chart host.
    renderTrailPills();
    renderProvenance(payload);
    const chartCard = repaintChartAndTable(payload);
    renderAnswer(payload, chartCard, seq, answerBody, opts.skipTypewriter);
    renderKeepFooter(payload);
    renderFollowUp();
    hasAnswer = true;
    announce("Answer ready");
  }

  // A wait state for the answer body while the commentary streams in: the same
  // blinking cursor the loading state uses, held below the already-painted
  // provenance + chart + table until the `done` event arrives.
  function renderAnswerWaiting(bodyEl) {
    bodyEl.innerHTML =
      '<span class="dashdown-ask-cursor" aria-hidden="true"></span>';
  }

  // Consume a staged SSE ask response (POST /api/ask with stream:true). Two
  // events: `resolved` paints the full panel skeleton — provenance/chips, chart,
  // table, keep footer, follow-up — with the answer body in a wait state; `done`
  // merges the commentary into the trail entry, wires chart annotations, and
  // typewriters the answer in. `error` (after headers) surfaces a fresh ask's
  // error card, or — for a follow-up — the inline error slot without blowing
  // away the current answer. `dismissed`/`requestSeq` are re-checked on every
  // event so a superseded or closed panel never paints. The frame parser + the
  // stale-aware read loop live in core.js (readSseFrames); this only maps events.
  async function consumeAskStream(response, question, seq, fresh) {
    let entry = null; // this ask's trail entry (pushed on `resolved`)
    let chartCard = null;

    await readSseFrames(response, {
      isStale: () => dismissed || seq !== requestSeq,
      onEvent: (event, data) => {
        if (event === "resolved") {
          if (dismissed || seq !== requestSeq) return;
          // New trail entry for this ask (a fresh header ask already cleared the
          // trail; a follow-up appends). Refinement/keep/follow-up all read the
          // current (last) entry, so this is what those paths edit in place. Only
          // the `resolved` event rebuilds the skeleton — a follow-up's current
          // answer stays fully visible until this lands.
          entry = pushTrail({ question, payload: data });
          // A successful answer records the question for the "Recent" section
          // and — a fresh session now being live — retires any "Continue" row.
          pushRecent(question);
          delete el.dataset.askResume;
          buildAnswerSkeleton();
          applyModelTooltip(data);
          renderTrailPills();
          renderProvenance(data);
          chartCard = repaintChartAndTable(data);
          renderAnswerWaiting(answerBody);
          renderKeepFooter(data);
          renderFollowUp();
          hasAnswer = true;
          announce("Data ready — writing commentary");
        } else if (event === "done") {
          if (dismissed || seq !== requestSeq || !entry) return;
          setBusy(false);
          // Merge the commentary into the (partial) resolved payload so the trail
          // entry carries the full answer for keep / follow-up context.
          const full = { ...entry.payload, ...data };
          entry.payload = full;
          persistSession();
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
            showExecError(msg);
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

    // A follow-up failure never blows away the visible answer: surface it through
    // the inline error slot (429 / notice / network / non-stream error) and
    // re-enable the input (its text is untouched, so edit-and-retry works). A
    // fresh ask keeps the full-shell error rendering (its trail is already clear).
    const fail = (msg) => {
      if (fresh) {
        renderError(msg);
      } else {
        showFollowupBusy(false);
        showExecError(msg);
      }
    };
    const failNotice = (msg) => {
      if (fresh) {
        renderNotice(msg);
      } else {
        showFollowupBusy(false);
        showExecError(msg);
      }
    };

    try {
      const reqBody = { question, params: gatherParams(), stream: true };
      if (opts.history && opts.history.length) reqBody.history = opts.history;
      const response = await postJson(_ASK_URL, reqBody, {
        signal: controller.signal,
      });
      if (dismissed || seq !== requestSeq) return; // closed / newer question took over
      // Staged SSE (the normal cache-miss / cache-hit path) vs. plain JSON (a
      // 429 / notice / disabled / proxy fallback — checked before streaming).
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
      // A non-streaming JSON answer (shouldn't happen on the happy path, but stay
      // robust): render it whole like the pre-staging client did.
      pushTrail({ question, payload: data });
      pushRecent(question);
      delete el.dataset.askResume;
      renderAnswerPayload(data, seq);
    } catch (error) {
      if (dismissed || seq !== requestSeq || (error && error.name === "AbortError")) {
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
  // (clears the trail, no history) — refinement/follow-up carries the trail,
  // not this.
  el.addEventListener("dashdown:ask", (ev) => {
    const q = ((ev.detail && ev.detail.question) || "").trim();
    if (q) submit(q, { fresh: true });
  });

  // Escape closes the answer panel *first*, then falls through to search's own
  // Escape on a second press. Capture phase on `el` (an ancestor of the input)
  // fires before site_search.js's target-phase keydown, so stopPropagation keeps
  // the first Escape from also closing/blurring search. The follow-up field has
  // its own layering (its Escape steps focus back to the omnibox input, then a
  // second Escape lands here and closes).
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
      // on the freshly re-initialized instance too (a bare updateChart here was
      // the theme-toggle regression: the axis name reappeared and clipped).
      paintPanelChart(card, records, chartState.config);
    }
  });

  // ---- Restored session + deep link (4A-b / 4A-c) -------------------------

  // site_search.js pulls a fresh resume value right before rendering the
  // empty-focus dropdown; expose the stored session's last question then.
  el.addEventListener("dashdown:ask-resume", refreshResume);
  // And seed it once at init (before any interaction), so a straight focus after
  // reload already offers the "Continue" row.
  refreshResume();

  // Deep-link prefill: ?_ask=<question> fills the omnibox, focuses + selects it,
  // and waits for the operator to press Enter — NEVER auto-submits (the approved
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
