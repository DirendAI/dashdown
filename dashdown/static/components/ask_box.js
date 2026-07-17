// Dashdown Header Ask Box
//
// A global operator "ask box" in the app header: type a question, press Enter,
// and a dropdown panel answers it with provenance + an auto-inferred chart +
// a result table + a typewriter answer. It POSTs the question to the runtime
// ask endpoint (POST /_dashdown/api/ask), which resolves it against the
// project's semantic models / named queries and returns a single JSON payload
// (see ARCHITECTURE.md §B for the contract).
//
// This is the free-form sibling of the authored <Ask /> card (ask.js): it reuses
// the same typewriter feel, the same chart-annotation ref chips, and the same
// chart/table renderers, but the question is the operator's, not the author's.
//
// The box is gated server-side (`{% if ask_enabled %}` — llm on ∧ ask on ∧ not
// embed), so it never appears in static builds or embeds. The panel is only
// built on user interaction, so a headless print/screenshot run (which never
// types a question) is untouched.

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
import { setChartAnnotations, emphasizeChartAnnotation } from "./annotations.js";
import { renderTableInto } from "./table.js";

const _REPLAY_TICK_MS = 30; // typewriter cadence (matches ask.js)
const _REPLAY_CAP_MS = 2500; // whole replay finishes within this budget
const _ASK_URL = "/_dashdown/api/ask";

/** ✦ AI badge markup — mirrors the authored ask card's provenance sparkle. */
const _AI_BADGE =
  '<span class="dashdown-ask-badge dashdown-ask-box-badge" title="AI-generated answer">' +
  '<span class="dashdown-ask-badge-text">✦ AI</span></span>';

/**
 * Non-empty, non-internal filter values for the request body. Query SQL is never
 * shipped to the client, so every active filter is sent; the server keys its
 * answer cache on only the params each resolution actually uses.
 * @param {Object} filters
 * @returns {Object} - filter name -> string value
 */
function relevantFilters(filters) {
  const out = {};
  for (const k of Object.keys(filters || {})) {
    if (k.startsWith("_")) continue;
    const v = filters[k];
    if (v == null || String(v) === "") continue;
    out[k] = String(v);
  }
  return out;
}

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

/**
 * Initialize one header ask box.
 * @param {HTMLElement} el - The `.dashdown-ask-box` wrapper.
 */
export function initAskBox(el) {
  const input = el.querySelector(".dashdown-ask-box-input");
  const panel = el.querySelector(".dashdown-ask-box-panel");
  const field = el.querySelector(".dashdown-ask-box-field");
  if (!input || !panel) return;

  let requestSeq = 0; // drop responses a newer question superseded
  let abortController = null;
  let hasAnswer = false; // panel holds a rendered answer (for reopen)
  // The live panel chart, so a theme toggle can dispose + re-init it (it's not
  // in chart.js's registry, so onThemeChange there won't reach it).
  let chartState = null; // { card, container, config }

  function setBusy(busy) {
    if (field) field.classList.toggle("dashdown-ask-box-busy", busy);
    input.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function open() {
    if (panel.hidden) {
      panel.hidden = false;
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
  }

  function panelShell() {
    resetPanel();
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
    panel.appendChild(header);
    open();
    return panel;
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

  // Hovering/focusing a ref chip in the answer bolds the chart mark it cites,
  // exactly like the authored ask card (ask.js::wireRefChips). No-op when there
  // is no chart host.
  function wireRefChips(bodyEl, chartCard) {
    if (!chartCard) return;
    bodyEl.querySelectorAll(".dashdown-anno-ref").forEach((chip) => {
      const id = chip.dataset.annoId;
      if (!id) return;
      const bold = () => emphasizeChartAnnotation(chartCard, id);
      const restore = () => emphasizeChartAnnotation(chartCard, null);
      chip.addEventListener("mouseenter", bold);
      chip.addEventListener("mouseleave", restore);
      chip.addEventListener("focus", bold);
      chip.addEventListener("blur", restore);
    });
  }

  // Build a chart host that speaks the same _chartConfig/_echarts_instance/
  // _chartInstance contract the annotation helpers expect, so setChartAnnotations
  // + emphasizeChartAnnotation work unchanged against it.
  function renderChart(payload) {
    const records = recordsOf(payload);
    const spec = payload.chart || {};
    const config = {
      type: spec.type,
      x: spec.x,
      y: spec.y,
      title: spec.title || "",
    };
    const card = document.createElement("div");
    card.className = "dashdown-chart dashdown-ask-box-chart";
    card.innerHTML = '<div class="dashdown-chart-container dashdown-ask-box-chart-container"></div>';
    panel.appendChild(card);
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
          updateChart(card, recs, config);
          instance.setOption({ yAxis: { name: "" } });
        }
      },
    };
    chartState = { card, container, config };

    updateChart(card, records, config);
    // The compact panel has no headroom for the y-axis name ECharts draws
    // above the axis (it clips against the provenance line) — and the
    // provenance + table header already name the metric. Merge it away.
    instance.setOption({ yAxis: { name: "" } });
    if (Array.isArray(payload.annotations) && payload.annotations.length) {
      setChartAnnotations(card, payload.annotations);
    }
    return card;
  }

  function renderTable(payload) {
    const host = document.createElement("div");
    host.className = "dashdown-table dashdown-ask-box-table";
    panel.appendChild(host);
    renderTableInto(host, recordsOf(payload), {
      page_size: 10,
      export: false,
      search: false,
      fullscreen: false,
    });
  }

  // Type the answer out word-batched (the ask.js replay cadence), then swap in
  // the sanitized answer_html and wire its ref chips. Reduced-motion skips
  // straight to the final HTML.
  function renderAnswer(payload, chartCard, seq) {
    const body = document.createElement("div");
    body.className = "dashdown-ask-body dashdown-ask-box-body";
    panel.appendChild(body);

    const finish = () => {
      body.innerHTML = payload.answer_html || esc(payload.answer_text || "");
      wireRefChips(body, chartCard);
    };

    const text = payload.answer_text || "";
    const words = text.match(/\S+\s*/g) || [];
    if (!words.length || prefersReducedMotion()) {
      finish();
      return;
    }
    const perTick = Math.max(
      1,
      Math.ceil(words.length / (_REPLAY_CAP_MS / _REPLAY_TICK_MS))
    );
    const streamEl = document.createElement("div");
    streamEl.className = "dashdown-ask-stream";
    body.appendChild(streamEl);
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

  function renderAnswerPayload(payload, seq) {
    panelShell();

    const resolved = payload.resolved || {};
    if (resolved.provenance) {
      const prov = document.createElement("div");
      prov.className = "dashdown-ask-box-provenance";
      prov.textContent = resolved.provenance;
      panel.appendChild(prov);
    }

    let chartCard = null;
    if (payload.chart && payload.columns && payload.rows && payload.rows.length) {
      try {
        chartCard = renderChart(payload);
      } catch (e) {
        console.error("dashdown ask box: chart render failed", e);
        chartCard = null;
      }
    }

    if (payload.columns && payload.rows && payload.rows.length) {
      try {
        renderTable(payload);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
    }

    renderAnswer(payload, chartCard, seq);
    hasAnswer = true;
  }

  async function submit(question) {
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    setBusy(true);
    renderLoading();

    try {
      const response = await postJson(_ASK_URL, {
        question,
        params: gatherParams(),
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
      renderAnswerPayload(data, seq);
    } catch (error) {
      if (seq !== requestSeq || (error && error.name === "AbortError")) return;
      setBusy(false);
      console.error("dashdown ask box: request failed", error);
      renderError(error && error.message);
    }
  }

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      const q = input.value.trim();
      if (q) submit(q);
    } else if (ev.key === "Escape") {
      if (!panel.hidden) {
        ev.preventDefault();
        close();
      } else {
        input.blur();
      }
    }
  });

  // Reopening re-shows the last answer without re-asking. Both focus AND click
  // are wired: after Esc-close the input keeps focus, so a later click on the
  // still-focused field fires no `focus` event — the click handler covers it.
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
      updateChart(card, records, chartState.config);
    }
  });
}

/**
 * Initialize every header ask box on the page (there is normally one).
 */
export function initAllAskBoxes() {
  document.querySelectorAll(".dashdown-ask-box").forEach((el) => initAskBox(el));
}
