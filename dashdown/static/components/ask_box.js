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
  recordsOf,
} from "../core.js";
import { currentEChartsTheme, onThemeChange } from "./echarts_theme.js";
import { updateChart } from "./chart.js";
import { setChartAnnotations } from "./annotations.js";
import { renderTableInto } from "./table.js";
import {
  _REPLAY_TICK_MS,
  _REPLAY_CAP_MS,
  relevantFilters,
  wireAnnotationRefChips,
} from "./ask.js";

const _ASK_URL = "/_dashdown/api/ask";
const _EXECUTE_URL = "/_dashdown/api/ask/execute";
const _KEEP_URL = "/_dashdown/api/ask/keep";

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

function prefersReducedMotion() {
  return (
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
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

  let requestSeq = 0; // drop responses a newer question/execute superseded
  let abortController = null;
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
  };

  // Rebuilt on each answer render (buildAnswerSkeleton); the refinement paths
  // target these stable slots instead of the whole panel.
  let slots = null; // { trail, prov, err, bodyWrap, chart, table, keep, followup }
  let answerBody = null; // the .dashdown-ask-body that holds the typed prose
  let updateBtn = null; // "↻ Update commentary" (revealed when prose is stale)
  let followupInput = null; // the bottom "refine or follow-up" field

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
    panel.hidden = true;
    el.classList.remove("dashdown-ask-answer-open");
    input.setAttribute("aria-expanded", "false");
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
    hasAnswer = false;
    slots = null;
    answerBody = null;
    updateBtn = null;
    followupInput = null;
  }

  // Topbar (badge + close) shared by the transient states and the full answer.
  function buildTopbar() {
    const header = document.createElement("div");
    header.className = "dashdown-ask-box-topbar";
    header.innerHTML =
      _AI_BADGE +
      '<button type="button" class="dashdown-ask-box-close" aria-label="Close answer">✕</button>';
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
    hasAnswer = true;
  }

  function renderNotice(message) {
    panelShell();
    const div = document.createElement("div");
    div.className = "dashdown-ask-notice dashdown-ask-box-message";
    div.textContent = message || "Ask is unavailable";
    panel.appendChild(div);
    hasAnswer = true;
  }

  // Inline (non-destructive) error for the refinement paths — a 429/rate-limit
  // or an invalid-spec 400 shows here without blowing away the answer.
  function showExecError(message) {
    if (!slots || !slots.err) return;
    slots.err.textContent = message || "Refine request failed";
    slots.err.hidden = false;
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

  // Rebuild the chart + table slots from a payload. Returns the chart card (or
  // null when the payload carries no chart / no data) so the answer's annotation
  // ref chips can wire against it. Shared by the first render and every refine.
  function repaintChartAndTable(payload) {
    disposeChart();
    slots.chart.innerHTML = "";
    slots.table.innerHTML = "";
    let chartCard = null;
    const hasData = payload.columns && payload.rows && payload.rows.length;
    if (payload.chart && hasData) {
      try {
        chartCard = renderChart(payload, slots.chart);
      } catch (e) {
        console.error("dashdown ask box: chart render failed", e);
        chartCard = null;
      }
    }
    if (hasData) {
      try {
        renderTable(payload, slots.table);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
    }
    return chartCard;
  }

  // Type the answer out word-batched (the ask.js replay cadence) into `bodyEl`,
  // then swap in the sanitized answer_html and wire its ref chips. Reduced-motion
  // (or `skipTypewriter`, used when restoring a stored answer from the trail)
  // skips straight to the final HTML.
  function renderAnswer(payload, chartCard, seq, bodyEl, skipTypewriter) {
    bodyEl.innerHTML = "";

    const finish = () => {
      bodyEl.innerHTML = payload.answer_html || esc(payload.answer_text || "");
      wireAnnotationRefChips(bodyEl, chartCard);
    };

    const text = payload.answer_text || "";
    const words = text.match(/\S+\s*/g) || [];
    if (!words.length || prefersReducedMotion() || skipTypewriter) {
      finish();
      return;
    }
    const perTick = Math.max(
      1,
      Math.ceil(words.length / (_REPLAY_CAP_MS / _REPLAY_TICK_MS))
    );
    const streamEl = document.createElement("div");
    streamEl.className = "dashdown-ask-stream";
    bodyEl.appendChild(streamEl);
    let i = 0;
    const tick = () => {
      if (seq !== requestSeq) return; // superseded — stop typing
      streamEl.textContent += words.slice(i, i + perTick).join("");
      i += perTick;
      if (i >= words.length) {
        finish();
        return;
      }
      setTimeout(tick, _REPLAY_TICK_MS);
    };
    tick();
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
      onChipChange();
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
      onChipChange();
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
          onChipChange();
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
        onChipChange();
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
          onChipChange();
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
              onChipChange();
            }
          )
        );
      }
    }

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
      if (seq !== requestSeq) return null;
      const data = await response.json().catch(() => null);
      if (seq !== requestSeq) return null;
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
      if (seq !== requestSeq || (error && error.name === "AbortError")) {
        return null;
      }
      setBusy(false);
      return { error: (error && error.message) || "Refine request failed", seq };
    }
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
  // markdown, so the operator's ad-hoc question becomes a permanent card. Only
  // for answers that resolved to a semantic metric or a named query (`resolved.kind`
  // "semantic"/"query") — a raw-SQL answer has no stable, re-runnable reference to
  // embed. Gated by the box's `ask_keep` config flag (server: `ask_keep_enabled`).
  // Rebuilt on every render/refine so it always keeps the *current* edited spec
  // (it posts the current trail entry's `resolved`, which the server re-validates).
  function renderKeepFooter(payload) {
    slots.keep.innerHTML = "";
    if (!config.ask_keep) return;
    const resolved = payload.resolved || {};
    if (resolved.kind !== "semantic" && resolved.kind !== "query") return;

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
  }

  async function submit(question, opts = {}) {
    // A fresh header ask starts a new session; a follow-up keeps the trail and
    // appends its answer on success.
    if (opts.fresh) trail = [];
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    setBusy(true);
    renderLoading();

    try {
      const reqBody = { question, params: gatherParams() };
      if (opts.history && opts.history.length) reqBody.history = opts.history;
      const response = await postJson(_ASK_URL, reqBody, {
        signal: controller.signal,
      });
      if (seq !== requestSeq) return; // a newer question took over
      const data = await response.json().catch(() => null);
      if (seq !== requestSeq) return;
      setBusy(false);
      if (!data) {
        renderError(`HTTP ${response.status}`);
        return;
      }
      if (data.notice) {
        renderNotice(data.notice);
        return;
      }
      if (!response.ok || data.error) {
        renderError(data.error || data.detail || `HTTP ${response.status}`);
        return;
      }
      trail.push({ question, payload: data });
      renderAnswerPayload(data, seq);
    } catch (error) {
      if (seq !== requestSeq || (error && error.name === "AbortError")) return;
      setBusy(false);
      console.error("dashdown ask box: request failed", error);
      renderError(error && error.message);
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

  // Click-away closes the panel (leaves its content for the next reopen).
  document.addEventListener("click", (ev) => {
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
