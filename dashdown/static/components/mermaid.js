// Offline Mermaid diagrams.
//
// The server-side highlighter (render/markdown.py::highlight_code) emits
// a ```mermaid fence as a plain marker block —
//   <pre class="dashdown-code dashdown-mermaid" data-lang="mermaid"><code>…</code></pre>
// — instead of syntax-highlighting it. This module finds those blocks and
// upgrades them to SVG diagrams in the browser, themed to match the active
// light/dark theme.
//
// Two constraints from CLAUDE.md drive the design:
//   - **Offline (no CDN):** the Mermaid bundle is vendored at
//     static/vendor/mermaid.min.js (the self-contained IIFE build that assigns
//     globalThis.mermaid), resolved relative to this module so it works on the
//     dev server and in base-resolved static exports alike (same pattern as
//     chart.js's world.json).
//   - **Lazy-load:** the bundle is ~3MB, so the <script> is injected only when a
//     page actually contains a mermaid block — diagram-free pages never pay for it.
//
// Rendering is purely client-side from the page HTML (no data API), so diagrams
// work in `dashdown build` static exports and chrome-less embeds with no server
// round-trip. If the bundle fails to load or a diagram is invalid, the original
// source stays visible (graceful degradation).

"use strict";

import { onThemeChange } from "./echarts_theme.js";

// Resolved against this module's served URL — robust to the dev server and the
// static build's runtime <base> alike (cf. chart.js DEFAULT_GEOJSON_URLS).
const MERMAID_URL = new URL("../vendor/mermaid.min.js", import.meta.url).href;

const BLOCK_SELECTOR = 'pre[data-lang="mermaid"]';
const DIAGRAM_CLASS = "dashdown-mermaid-diagram";

let _loadPromise = null;
let _renderSeq = 0;
let _themeBound = false;

/** Inject the vendored Mermaid bundle once; resolve with `window.mermaid`. */
function loadMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (_loadPromise) return _loadPromise;
  _loadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = MERMAID_URL;
    script.async = true;
    script.onload = () =>
      window.mermaid
        ? resolve(window.mermaid)
        : reject(new Error("mermaid.min.js loaded but window.mermaid is undefined"));
    script.onerror = () => reject(new Error(`failed to load ${MERMAID_URL}`));
    document.head.appendChild(script);
  });
  return _loadPromise;
}

// Inter-led stack matching the rest of the app (a concrete value, not "inherit":
// Mermaid measures label widths against this font, and SVG <text> can't inherit
// from the page anyway).
const FONT_STACK =
  'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

// Palette pinned to the app's indigo+slate light/dark surfaces (kept in sync with
// the DaisyUI theme vars / echarts_theme.js). Mermaid's `base` theme is the one
// designed to be driven entirely by themeVariables.
const THEME_VARS = {
  light: {
    primaryColor: "#eef2ff", // indigo-50 node fill
    primaryTextColor: "#0f172a", // slate-900 label text
    primaryBorderColor: "#6366f1", // indigo-500 node border
    lineColor: "#94a3b8", // slate-400 edges/arrows
    secondaryColor: "#f1f5f9",
    tertiaryColor: "#f8fafc",
    textColor: "#0f172a",
    mainBkg: "#eef2ff",
    edgeLabelBackground: "#ffffff",
    clusterBkg: "#f8fafc",
    clusterBorder: "#cbd5e1",
    // Sequence diagrams.
    actorBkg: "#eef2ff",
    actorBorder: "#6366f1",
    actorTextColor: "#0f172a",
    actorLineColor: "#94a3b8",
    signalColor: "#475569",
    signalTextColor: "#0f172a",
    labelBoxBkgColor: "#eef2ff",
    labelBoxBorderColor: "#6366f1",
    labelTextColor: "#0f172a",
    noteBkgColor: "#fef9c3",
    noteTextColor: "#0f172a",
    noteBorderColor: "#fde047",
  },
  dark: {
    darkMode: true,
    primaryColor: "#1e293b", // slate-800 node fill
    primaryTextColor: "#f1f5f9", // slate-100 label text
    primaryBorderColor: "#818cf8", // indigo-400 node border
    lineColor: "#64748b", // slate-500 edges/arrows
    secondaryColor: "#334155",
    tertiaryColor: "#0f172a",
    textColor: "#e2e8f0",
    mainBkg: "#1e293b",
    edgeLabelBackground: "#0f172a",
    clusterBkg: "#0f172a",
    clusterBorder: "#334155",
    // Sequence diagrams.
    actorBkg: "#1e293b",
    actorBorder: "#818cf8",
    actorTextColor: "#f1f5f9",
    actorLineColor: "#475569",
    signalColor: "#94a3b8",
    signalTextColor: "#e2e8f0",
    labelBoxBkgColor: "#1e293b",
    labelBoxBorderColor: "#818cf8",
    labelTextColor: "#f1f5f9",
    noteBkgColor: "#334155",
    noteTextColor: "#f1f5f9",
    noteBorderColor: "#475569",
  },
};

function configure(mermaid) {
  const dark = document.documentElement.getAttribute("data-theme") === "dark";
  mermaid.initialize({
    startOnLoad: false,
    theme: "base",
    themeVariables: { fontFamily: FONT_STACK, ...THEME_VARS[dark ? "dark" : "light"] },
    securityLevel: "strict",
    fontFamily: FONT_STACK,
    // Render labels as native SVG <text>, not HTML in a <foreignObject>. The
    // foreignObject path has two problems here: its <p>/<span> inherit the page's
    // .dashdown-prose CSS (which washed the text out to the muted --bc2 color),
    // and its rasterized HTML blurs when a wide diagram is scaled down to fit the
    // column. SVG text dodges both — correct color + crisp at any scale.
    htmlLabels: false,
    flowchart: { htmlLabels: false, useMaxWidth: true },
  });
}

/**
 * Convert any not-yet-upgraded <pre data-lang="mermaid"> into a diagram
 * container, stashing the diagram source so a theme change can re-render it.
 * `pre.textContent` decodes the HTML entities the server escaped, giving the
 * raw source Mermaid expects.
 */
function upgradeBlocks() {
  document.querySelectorAll(BLOCK_SELECTOR).forEach((pre) => {
    const container = document.createElement("div");
    container.className = DIAGRAM_CLASS;
    container.setAttribute("data-mermaid-source", pre.textContent || "");
    pre.replaceWith(container);
  });
}

/** Render (or re-render, on theme change) every diagram from its stashed source. */
function renderAll(mermaid) {
  upgradeBlocks();
  document.querySelectorAll("." + DIAGRAM_CLASS).forEach((container) => {
    const source = container.getAttribute("data-mermaid-source") || "";
    // Unique id per render so concurrent/re-theme renders never collide on the
    // temporary node Mermaid creates internally.
    const id = "dashdown-mermaid-" + _renderSeq++;
    mermaid
      .render(id, source)
      .then(({ svg, bindFunctions }) => {
        container.classList.remove("dashdown-mermaid-failed");
        container.innerHTML = svg;
        if (typeof bindFunctions === "function") bindFunctions(container);
      })
      .catch((err) => {
        // Mermaid can leave an orphan node behind on a parse error — clean it up.
        const orphan = document.getElementById(id);
        if (orphan) orphan.remove();
        showDiagramError(container, source, err);
      });
  });
}

/** Replace a failed diagram with a short note + its source (graceful fallback). */
function showDiagramError(container, source, err) {
  console.error("Dashdown: mermaid diagram failed to render", err);
  container.classList.add("dashdown-mermaid-failed");
  container.innerHTML = "";
  const note = document.createElement("p");
  note.className = "dashdown-mermaid-error";
  note.textContent =
    "Diagram failed to render" + (err && err.message ? `: ${err.message}` : "");
  const pre = document.createElement("pre");
  pre.className = "dashdown-code";
  const code = document.createElement("code");
  code.textContent = source;
  pre.appendChild(code);
  container.appendChild(note);
  container.appendChild(pre);
}

/**
 * Upgrade every ```mermaid block on the page to an SVG diagram. Self-gating: if
 * the page has no mermaid blocks, the ~3MB bundle is never loaded. Safe to call
 * regardless of whether the page has any data components (diagrams are static
 * HTML, independent of the Alpine stores / data API).
 */
export function initAllMermaid() {
  if (!document.querySelector(BLOCK_SELECTOR)) return;
  loadMermaid()
    .then((mermaid) => {
      configure(mermaid);
      renderAll(mermaid);
      // Re-theme on light/dark toggle (single shared observer via echarts_theme).
      if (!_themeBound) {
        _themeBound = true;
        onThemeChange(() => {
          configure(mermaid);
          renderAll(mermaid);
        });
      }
    })
    .catch((err) => {
      // Bundle failed to load: leave the source code blocks visible and don't
      // throw — other components must still initialize.
      console.error("Dashdown: failed to load mermaid", err);
    });
}
