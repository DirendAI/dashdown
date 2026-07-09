// Dashdown Ask Component
// Authored LLM commentary on a query's data. Fetches the rendered answer from
// /_dashdown/api/ask/{id} (or the baked _ask/{id}.json snapshot in a static
// export), with a blinking-cursor wait state, debounced regeneration when filters the
// referenced query uses change, and an explicit ↻ refresh affordance.
//
// On a live server the request opts into streaming (`_stream=1`): a cache miss
// arrives as Server-Sent Events — `chunk` events append escaped plain text
// while the model writes, then one `done` event carries the server-rendered
// sanitized HTML that replaces it (model output is only ever rendered
// server-side with raw HTML disabled). A cache hit stays a single JSON
// payload, so the branch below is decided by the response Content-Type.
//
// A cached / static-baked answer carries the raw answer text (`text`) so it
// can be *replayed* with the same typewriter feel the live stream had: the
// text types out as escaped plain text (identical rendering to live chunks),
// then the sanitized HTML swaps in. Pacing is synthetic — a smooth cadence
// capped to a short total, not recorded wall-clock timings (real streams
// stall). Policy via the <Ask replay=…> attr: "once" (default; per session
// per answer, tracked in sessionStorage), "always", or "off";
// prefers-reduced-motion always skips straight to the final HTML.

"use strict";

import { readBuildConfig, readEmbedToken, readRouteParams, esc } from "../core.js";
import {
  clearChartAnnotations,
  emphasizeChartAnnotation,
  setChartAnnotations,
} from "./annotations.js";

const _DEBOUNCE_MS = 400;
const _REPLAY_TICK_MS = 30; // typewriter cadence for replayed answers
const _REPLAY_CAP_MS = 2500; // whole replay finishes within this budget

/**
 * Non-empty, non-internal filter values for the request URL. Query SQL is never
 * shipped to the client, so all active filters are sent; the server keys its
 * answer cache on only the params each query actually substitutes.
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
 * Initialize an ask component
 * @param {HTMLElement} el - Element with data-async-component="ask" (or a
 *   chart's explain footer, which carries the same config/markup shape)
 * @param {Object} [opts]
 * @param {() => boolean} [opts.paused] - When it returns true, filter-driven
 *   loads are deferred instead of fired (an LLM call for a hidden surface is
 *   a bill nobody reads). The returned handle's `flush()` fires the newest
 *   deferred load — call it when the surface is revealed again.
 * @returns {{flush: () => void} | undefined}
 */
export function initAsk(el, opts = {}) {
  const config = JSON.parse(el.dataset.config);
  const askId = config.ask_id;
  const body = el.querySelector(".dashdown-ask-body");
  const refreshBtn = el.querySelector(".dashdown-ask-refresh");
  const modelEl = el.querySelector(".dashdown-ask-model");
  if (!body) return;

  const loadingHtml = body.innerHTML; // the blinking-cursor wait state
  const build = readBuildConfig();
  const isStatic = !!(build && build.static);
  // A chart's explain footer sits inside the chart card it explains; the
  // payload's chart annotations are applied to that host. The SVG geo map
  // cards (.dashdown-map) speak the same _chartConfig/_chartInstance contract,
  // so they're hosts too. Null for a plain <Ask /> card — every annotation
  // call below then no-ops.
  const chartHost = el.closest(".dashdown-chart, .dashdown-map");
  const paused = opts.paused || (() => false);
  let requestSeq = 0; // drop responses that a newer request has superseded
  let abortController = null; // aborts the superseded in-flight fetch/stream
  let debounceTimer = null;
  let lastUrl = null;
  let pendingUrl = null; // load deferred while paused() held

  function urlFor(filters, refresh) {
    if (isStatic) {
      return `${build.dataBase}/_ask/${encodeURIComponent(askId)}.json`;
    }
    // Route params (lowest precedence) so an <Ask /> on a dynamic [slug] page
    // comments on that record's data — the answer cache then keys per record too.
    const params = new URLSearchParams({
      ...readRouteParams(),
      ...relevantFilters(filters),
    });
    params.set("_stream", "1"); // SSE on a cache miss; a hit still returns JSON
    if (refresh) params.set("_refresh", "1");
    const embedToken = readEmbedToken();
    if (embedToken) params.set("_embed", embedToken);
    const qs = params.toString();
    return `/_dashdown/api/ask/${encodeURIComponent(askId)}` + (qs ? `?${qs}` : "");
  }

  function renderError(message) {
    body.innerHTML = `<div class="dashdown-ask-error">${esc(message)}</div>`;
  }

  // An expected "commentary is off" state (no/broken `llm:` block) — not an
  // error. Muted note, refresh affordance stays hidden (nothing to retry).
  function renderNotice(message) {
    body.innerHTML = `<div class="dashdown-ask-notice">${esc(message)}</div>`;
  }

  // ---- Typewriter replay of a cached / static-baked answer ---------------

  // sessionStorage key for replay="once": the ask + its route params (a
  // dynamic [slug] page shares one ask id across records, and each record's
  // answer deserves its first showing). Deliberately NOT keyed on filters —
  // a filter-heavy dashboard would otherwise re-type on every combination,
  // which reads as noise rather than delivery.
  function replayStorageKey() {
    const route = new URLSearchParams(readRouteParams()).toString();
    return `dashdown-ask-replayed:${askId}${route ? `:${route}` : ""}`;
  }

  function shouldReplay(data) {
    if (!data.text) return false; // pre-replay server / old snapshot payload
    const mode = config.replay || "once";
    if (mode === "off") return false;
    if (
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      return false;
    }
    if (mode === "once") {
      try {
        if (sessionStorage.getItem(replayStorageKey())) return false;
      } catch {
        /* storage unavailable — replay anyway */
      }
    }
    return true;
  }

  // Called once the viewer has seen the full typing effect — a genuine live
  // stream counts too, so a later cache hit doesn't re-type the same answer.
  function markReplayed() {
    if ((config.replay || "once") !== "once") return;
    try {
      sessionStorage.setItem(replayStorageKey(), "1");
    } catch {
      /* storage unavailable */
    }
  }

  // Type the recorded answer out as escaped plain text (the exact rendering
  // the live SSE chunks get), then swap in the sanitized HTML via renderDone.
  // Pacing is synthetic: word-sized steps at a fixed cadence, batching words
  // per tick so long answers still finish inside _REPLAY_CAP_MS.
  function replayAnswer(data, seq) {
    const words = data.text.match(/\S+\s*/g) || [];
    if (!words.length) {
      renderDone(data);
      return Promise.resolve();
    }
    const perTick = Math.max(
      1,
      Math.ceil(words.length / (_REPLAY_CAP_MS / _REPLAY_TICK_MS))
    );
    const streamEl = document.createElement("div");
    streamEl.className = "dashdown-ask-stream";
    body.replaceChildren(streamEl);
    let i = 0;
    return new Promise((resolve) => {
      const tick = () => {
        if (seq !== requestSeq) return resolve(); // superseded — stop typing
        streamEl.textContent += words.slice(i, i + perTick).join("");
        i += perTick;
        if (i >= words.length) {
          renderDone(data);
          return resolve();
        }
        setTimeout(tick, _REPLAY_TICK_MS);
      };
      tick();
    });
  }

  function renderDone(data) {
    body.innerHTML = data.html || "";
    // Attribute the answer to the model that wrote it (server/build payload).
    if (modelEl && data.model) {
      modelEl.textContent = `Generated by ${data.model}`;
      modelEl.hidden = false;
    }
    // Regeneration needs the live endpoint; a static snapshot is fixed, so the
    // refresh affordance is revealed only on a live server.
    if (refreshBtn && !isStatic) refreshBtn.hidden = false;
    // Chart annotations (explain payloads only): paint the marks and remember
    // them so a close→reopen of the footer re-applies without a refetch
    // (initExplain reads _lastAnnotations). renderDone is the single consumer
    // of every response shape, so live JSON, cache hits, and static bakes all
    // land here identically.
    if (chartHost) {
      el._lastAnnotations = Array.isArray(data.annotations) ? data.annotations : [];
      if (el._lastAnnotations.length) {
        setChartAnnotations(chartHost, el._lastAnnotations);
      }
      wireRefChips();
    }
  }

  // Hovering/focusing a ref chip bolds the mark it cites (bolding only — no
  // dim-the-rest layer); leaving restores. The native <abbr title> tooltip
  // carries the label with zero JS, so this degrades gracefully.
  function wireRefChips() {
    body.querySelectorAll(".dashdown-anno-ref").forEach((chip) => {
      const id = chip.dataset.annoId;
      if (!id) return;
      const bold = () => emphasizeChartAnnotation(chartHost, id);
      const restore = () => emphasizeChartAnnotation(chartHost, null);
      chip.addEventListener("mouseenter", bold);
      chip.addEventListener("mouseleave", restore);
      chip.addEventListener("focus", bold);
      chip.addEventListener("blur", restore);
    });
  }

  // Consume an SSE cache-miss response: `chunk` events accumulate as escaped
  // plain text (textContent — the raw model stream never renders as HTML),
  // then `done` swaps in the server-rendered sanitized HTML.
  async function consumeStream(response, seq) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamEl = null;

    const handleEvent = (raw) => {
      let event = "message";
      const dataLines = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      let data;
      try {
        data = JSON.parse(dataLines.join("\n"));
      } catch {
        return;
      }
      if (event === "chunk") {
        if (!streamEl) {
          streamEl = document.createElement("div");
          streamEl.className = "dashdown-ask-stream";
          body.replaceChildren(streamEl);
        }
        streamEl.textContent += data.text || "";
      } else if (event === "done") {
        renderDone(data);
        // The viewer just watched the live stream — a later cache hit of the
        // same answer shouldn't re-type it (replay="once").
        markReplayed();
      } else if (event === "error") {
        renderError(data.error || "LLM request failed");
      }
    };

    for (;;) {
      const { done, value } = await reader.read();
      if (seq !== requestSeq) return; // stale — a newer request took over
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (raw.trim()) handleEvent(raw);
      }
    }
  }

  async function load(url) {
    const seq = ++requestSeq;
    if (abortController) abortController.abort();
    const controller = new AbortController();
    abortController = controller;
    body.innerHTML = loadingHtml;
    if (modelEl) modelEl.hidden = true; // re-hide while (re)loading
    // Params changed (or first load): the old marks describe old data — clear
    // them (and the reopen stash) until the new payload lands in renderDone.
    if (chartHost) {
      el._lastAnnotations = null;
      clearChartAnnotations(chartHost);
    }
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (seq !== requestSeq) return; // stale — a newer request is in flight
      const contentType = response.headers.get("Content-Type") || "";
      if (response.ok && contentType.includes("text/event-stream")) {
        await consumeStream(response, seq);
        return;
      }
      const data = await response.json().catch(() => null);
      if (seq !== requestSeq) return;
      if (!response.ok || !data || data.error) {
        renderError((data && (data.error || data.detail)) || `HTTP ${response.status}`);
        return;
      }
      if (data.notice) {
        renderNotice(data.notice);
        return;
      }
      // A cached / static-baked answer replays as a typewriter when the
      // policy allows; otherwise it renders instantly.
      if (shouldReplay(data)) {
        await replayAnswer(data, seq);
        if (seq === requestSeq) markReplayed(); // only a *finished* replay counts
      } else {
        renderDone(data);
      }
    } catch (error) {
      if (seq !== requestSeq || error.name === "AbortError") return;
      console.error(`Ask component error for ${askId}:`, error);
      renderError(error.message);
    }
  }

  function scheduleLoad(filters, refresh = false) {
    const url = urlFor(filters, refresh);
    if (!refresh && url === lastUrl) {
      // Nothing relevant changed — including filters churning away and back
      // while paused, so any intermediate deferred load is obsolete too.
      pendingUrl = null;
      return;
    }
    if (paused()) {
      // Newest wins; flush() fires it on reveal. Kill any timer armed while
      // the surface was still visible — this URL supersedes it.
      clearTimeout(debounceTimer);
      pendingUrl = url;
      return;
    }
    pendingUrl = null;
    lastUrl = url;
    clearTimeout(debounceTimer);
    if (requestSeq === 0 || refresh) {
      // First paint and explicit refresh fire immediately; only filter
      // churn is debounced.
      load(url);
    } else {
      debounceTimer = setTimeout(() => {
        // Re-check at fire time: the surface may have been hidden during the
        // debounce window (explain footer closed) — divert to pendingUrl so
        // the load waits for the next reveal instead of billing an LLM call
        // for a panel nobody is reading.
        if (paused()) {
          pendingUrl = url;
          return;
        }
        load(url);
      }, _DEBOUNCE_MS);
    }
  }

  // --- Provenance highlight -------------------------------------------
  // Hovering the ask glows the page elements bound to the queries it
  // comments on (every data component stamps data-query-name on its node).
  // Pure decoration, works in static exports too; config.highlight_queries
  // is [] when the author set highlight=false.
  const highlightQueries = config.highlight_queries || [];
  if (highlightQueries.length) {
    const selector = highlightQueries
      .map((q) => `[data-query-name="${CSS.escape(q)}"]`)
      .join(", ");
    let lit = [];
    el.addEventListener("mouseenter", () => {
      // Charts/tables only — never other ask cards, even on the same query.
      lit = [...document.querySelectorAll(selector)].filter(
        (n) => n !== el && n.dataset.asyncComponent !== "ask"
      );
      lit.forEach((n) => n.classList.add("dashdown-ask-highlight"));
    });
    el.addEventListener("mouseleave", () => {
      lit.forEach((n) => n.classList.remove("dashdown-ask-highlight"));
      lit = [];
    });
  }

  // The refresh affordance only works against a live server; a static export's
  // answer is a fixed baked snapshot, so leave the button hidden and unwired.
  if (refreshBtn && !isStatic) {
    refreshBtn.addEventListener("click", () => {
      const filters = (window.Alpine && Alpine.store("filters")) || {};
      scheduleLoad({ ...filters }, true);
    });
  }

  // Kick off the data flow: static snapshots fetch the baked answer once
  // (filters are ignored); live pages subscribe to the filters store via an
  // Alpine effect (same single reactive path as counter.js) — runs once
  // immediately, then on any filter change; the relevant-filter URL dedup
  // above skips refetches the query can't see.
  function start() {
    if (isStatic) {
      scheduleLoad({});
      return;
    }
    const subscribe = () => {
      Alpine.effect(() => {
        const filters = { ...(Alpine.store("filters") || {}) };
        scheduleLoad(filters);
      });
    };
    if (window.Alpine) {
      subscribe();
    } else {
      document.addEventListener("alpine:init", subscribe);
    }
  }

  // Lazy by default: an ask the viewer never scrolls to must not spend LLM
  // credits, so nothing loads (and no filter subscription runs) until the
  // card first approaches the viewport. Eager when the author set lazy=false,
  // in print/screenshot runs (headless Chromium never scrolls — the page must
  // settle for the readiness handshake), or without IntersectionObserver.
  const eager =
    config.lazy === false ||
    window.__dashdownPrint ||
    window.__dashdownCapture ||
    typeof IntersectionObserver === "undefined";
  if (eager) {
    start();
  } else {
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          io.disconnect();
          start();
        }
      },
      // Fire only once the card is genuinely on screen (a fifth visible) —
      // no pre-trigger, deliberately: generating early means a slow scroll
      // arrives at finished text and the viewer never sees the typing.
      { threshold: 0.2 }
    );
    io.observe(el);
  }

  return {
    flush() {
      if (pendingUrl === null) return;
      const url = pendingUrl;
      pendingUrl = null;
      lastUrl = url;
      clearTimeout(debounceTimer);
      load(url); // reveal is deliberate, like refresh — no debounce
    },
  };
}

/**
 * Initialize all ask components on the page
 */
export function initAllAsks() {
  document.querySelectorAll('[data-async-component="ask"]').forEach((el) => {
    initAsk(el);
  });
}

/**
 * Wire a chart's `explain` affordance: the ✨ button toggles the commentary
 * footer, and the footer's ask machinery is initialized only on the first
 * open — an unclicked chart never spends an LLM call. While the footer is
 * closed its filter subscription stays paused (see initAsk's `paused` gate),
 * so filter churn defers the regeneration until the next open instead of
 * billing for commentary nobody is reading.
 * @param {HTMLElement} el - The chart card hosting the button + footer
 */
export function initExplain(el) {
  const btn = el.querySelector(".dashdown-explain-btn");
  const panel = el.querySelector(".dashdown-explain-panel");
  if (!btn || !panel) return;
  let ask = null;
  btn.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (!open) {
      // Closing dismisses the commentary AND its marks — the chart returns to
      // its clean reading the moment the footer is gone.
      clearChartAnnotations(el);
      return;
    }
    if (!ask) {
      ask = initAsk(panel, { paused: () => panel.hidden }) || null;
    } else {
      // Reopen: re-apply the last payload's marks from client-side state (the
      // payload is already in hand — no refetch)…
      if (Array.isArray(panel._lastAnnotations) && panel._lastAnnotations.length) {
        setChartAnnotations(el, panel._lastAnnotations);
      }
      // …then let any load deferred while closed (filters changed) supersede.
      ask.flush();
    }
  });
}

/**
 * Initialize the explain affordance on every chart that has one — in static
 * exports too: the footer's ask surface reads the baked _ask/{id}.json
 * snapshot on first open (initAsk's static branch), so click → retrieve →
 * show works identically offline.
 */
export function initAllExplains() {
  document.querySelectorAll(".dashdown-explain-btn").forEach((btn) => {
    const host = btn.closest("[data-async-component]");
    if (host) initExplain(host);
  });
}
