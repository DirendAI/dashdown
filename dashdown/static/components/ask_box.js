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
import { setChartAnnotations } from "./annotations.js";
import { renderTableInto } from "./table.js";
import {
  _REPLAY_TICK_MS,
  _REPLAY_CAP_MS,
  relevantFilters,
  wireAnnotationRefChips,
} from "./ask.js";

const _ASK_URL = "/_dashdown/api/ask";

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
    // The server ships a concrete chart type, which skips chart.js's
    // resolveAutoConfig — where an auto-chart would derive sort_by for a temporal
    // x. So thread the server's sort hint through (temporal charts set sort_by=x)
    // or a time series renders in row order instead of by time.
    if (spec.sort_by) config.sort_by = spec.sort_by;
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
      wireAnnotationRefChips(body, chartCard);
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

    // Answer-first hierarchy: the operator asked a question, so the answer
    // text is the headline — it types in at the top while the evidence (chart,
    // then table) renders below it. With the panel's max-height scroll, what
    // scrolls out of view is detail, never the answer. The chart is built
    // before the answer only in DOM-insertion terms handled below: renderChart
    // must run first so the answer's annotation ref chips have a chart host to
    // wire against, but its card is inserted *after* the answer body.
    let chartCard = null;
    const hasData = payload.columns && payload.rows && payload.rows.length;
    if (payload.chart && hasData) {
      try {
        chartCard = renderChart(payload);
      } catch (e) {
        console.error("dashdown ask box: chart render failed", e);
        chartCard = null;
      }
    }

    renderAnswer(payload, chartCard, seq);
    if (chartCard) panel.appendChild(chartCard); // move below the answer body

    if (hasData) {
      try {
        renderTable(payload);
      } catch (e) {
        console.error("dashdown ask box: table render failed", e);
      }
    }
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
      const response = await postJson(
        _ASK_URL,
        {
          question,
          params: gatherParams(),
        },
        { signal: controller.signal }
      );
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

  // Ctrl/Cmd+K focuses the ask box from anywhere (search owns "/"). The hint
  // chip in the markup ships "Ctrl K"; swap it for the Mac glyph when apt.
  const hint = el.querySelector(".dashdown-ask-box-hint");
  const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  if (hint && isMac) hint.textContent = "⌘K";
  document.addEventListener("keydown", (ev) => {
    if (ev.key.toLowerCase() === "k" && (ev.metaKey || ev.ctrlKey) && !ev.altKey) {
      ev.preventDefault();
      input.focus();
      input.select();
    }
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
 * Initialize every header ask box on the page (there is normally one).
 */
export function initAllAskBoxes() {
  document.querySelectorAll(".dashdown-ask-box").forEach((el) => initAskBox(el));
}
