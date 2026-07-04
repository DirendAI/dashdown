// Dashdown print / PDF export support.
//
// Activated only when the page is loaded for PDF export — the `dashdown pdf`
// runner sets `window.__dashdownPrint = true` via a Playwright init script
// (or you can preview with `?_print=1`). When active it:
//
//   1. Adds the `dashdown-print` class to <html> so the print stylesheet in
//      dashdown.css takes over (vertical grid, hidden chrome, cover/section
//      styling — see "PDF export" there).
//   2. Injects a gradient cover page keyed to the branding palette.
//   3. Exposes a readiness signal (`window.__dashdownPrintReady`) the runner
//      polls before calling Chromium's print — charts draw asynchronously, so
//      printing too early yields blank canvases.
//
// The same readiness handshake (`window.__dashdownPrintReady`) is reused by
// `dashdown screenshot` via a `window.__dashdownCapture` flag — capture mode
// arms the signal WITHOUT the print dressing, so a screenshot looks like the
// live app. Everything is a no-op outside print/capture mode, so it costs
// ordinary viewers nothing. The chart-animation kill switch lives in chart.js
// (it reads both flags) so a captured chart is final immediately.

"use strict";

import { readBrandingConfig, readQueryDefs, readBuildConfig, esc } from "../core.js";
import { openExportModal } from "./export_modal.js";

const READY_FLAG = "__dashdownPrintReady";
// Fallback so a stuck/erroring/live component can't hang the export forever.
const MAX_WAIT_MS = 12000;
// Brief settle after data lands + canvases exist, so the final paint is in.
const SETTLE_MS = 350;
// Component types that fetch their data on load (so we wait for them). Filter
// controls are stripped from static exports; export buttons fetch on click.
const DATA_TYPES = ["chart", "table", "value", "counter", "pivot"];

/** True when the page was opened for PDF export. */
function isPrint() {
  return (
    !!window.__dashdownPrint ||
    new URLSearchParams(window.location.search).has("_print")
  );
}

/**
 * True when the page was opened for a headless screenshot (`dashdown screenshot`
 * sets `window.__dashdownCapture`). Capture mode reuses the SAME readiness
 * handshake as print, but does NOT dress the page for print — a screenshot
 * should look like the live, interactive app, not the print deck.
 */
function isCapture() {
  return !!window.__dashdownCapture;
}

/**
 * The set of query names whose first load we wait for. Read straight from the
 * server-rendered DOM + query-defs JSON, so it works before Alpine boots and
 * before any fetch fires. Live queries are skipped — they don't fetch in a
 * static export, so waiting for them would always time out.
 */
function expectedQueries() {
  const defs = readQueryDefs();
  const names = new Set();
  document.querySelectorAll("[data-async-component]").forEach((el) => {
    if (!DATA_TYPES.includes(el.getAttribute("data-async-component"))) return;
    let cfg;
    try {
      cfg = JSON.parse(el.dataset.config || "{}");
    } catch (e) {
      return;
    }
    const q = cfg.query_name;
    if (!q) return;
    const d = defs[q];
    if (d && d.live) return;
    names.add(q);
  });
  return names;
}

/** Every chart placeholder has drawn its canvas (or surfaced an error card).
 * Scoped to the ECharts container: the chart root can carry other SVGs (the
 * `explain` button + its footer's AI badge), which must not count as "drew". */
function chartsRendered() {
  const charts = document.querySelectorAll('[data-async-component="chart"]');
  for (const el of charts) {
    if (el.querySelector(".dashdown-error, .dashdown-error-card")) continue;
    if (
      !el.querySelector(
        ".dashdown-chart-container canvas, .dashdown-chart-container svg"
      )
    )
      return false;
  }
  return true;
}

function dateLabel() {
  try {
    return new Date().toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
    });
  } catch (e) {
    return "";
  }
}

/**
 * Insert a gradient cover page at the top of the main column. Reuses the
 * server-rendered titles (brand + page heading) and the branding palette, so
 * there's no second render path — it's the same static HTML, dressed for print.
 */
function buildCover() {
  const main = document.querySelector(".dashdown-main");
  if (!main || main.querySelector(".dashdown-print-cover")) return;

  const brand = document.querySelector(".dashdown-brand-title");
  const projectTitle = brand
    ? brand.textContent.trim()
    : (document.title.split("·").pop() || "").trim();

  const h1 = document.querySelector(".dashdown-prose h1");
  const pageTitle = h1
    ? h1.textContent.trim()
    : (document.title.split("·")[0] || "").trim();

  const palette = (readBrandingConfig() || {}).palette || [];
  const from = palette[0] || "#4f46e5";
  const to = palette[1] || palette[0] || "#312e81";

  const cover = document.createElement("section");
  cover.className = "dashdown-print-cover";
  cover.style.setProperty("--dashdown-cover-from", from);
  cover.style.setProperty("--dashdown-cover-to", to);
  cover.innerHTML =
    '<div class="dashdown-print-cover-inner">' +
    (projectTitle
      ? `<div class="dashdown-print-cover-project">${esc(projectTitle)}</div>`
      : "") +
    `<h1 class="dashdown-print-cover-title">${esc(pageTitle)}</h1>` +
    `<div class="dashdown-print-cover-date">${esc(dateLabel())}</div>` +
    "</div>";

  main.insertBefore(cover, main.firstChild);
}

/**
 * Dress the page for print: force the light (printable) theme, add the print
 * stylesheet class, and inject the gradient cover. Forcing light is deliberate —
 * a reader viewing the dashboard in dark mode would otherwise print near-white
 * text on background-less white paper. The `data-theme` change is observed by
 * echarts_theme.js, which disposes + re-inits every live chart with the light
 * ECharts theme (animation-free, since `window.__dashdownPrint` is set first).
 */
function enterPrintMode() {
  document.documentElement.setAttribute("data-theme", "light");
  document.documentElement.classList.add("dashdown-print");
  buildCover();
}

/** Restore the interactive view after an on-demand print. */
function leavePrintMode(prevTheme, scrollY) {
  document.documentElement.classList.remove("dashdown-print");
  if (prevTheme == null) document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", prevTheme);
  const cover = document.querySelector(".dashdown-print-cover");
  if (cover) cover.remove();
  const pageStyle = document.getElementById("dashdown-print-page");
  if (pageStyle) pageStyle.remove();
  document.documentElement.style.removeProperty("--dashdown-print-width");
  document.documentElement.style.removeProperty("--dashdown-print-height");
  if (typeof scrollY === "number") window.scrollTo(0, scrollY);
}

// Page geometry, kept in lock-step with the server engine (pdf.py): same page
// sizes and asymmetric margins (L/R 12mm, T/B 14mm) so the client window.print()
// fallback produces the same layout as `dashdown pdf`.
const PAGE_MM = {
  A4: [210, 297],
  A3: [297, 420],
  Letter: [215.9, 279.4],
  Legal: [215.9, 355.6],
};
const MARGIN_LR_MM = 12;
const MARGIN_TB_MM = 14;
const PX_PER_MM = 96 / 25.4;

/** Printable area (CSS px) for a page format/orientation — page minus margins. */
function printableArea(orientation, format) {
  let [w, h] = PAGE_MM[format] || PAGE_MM.A4;
  if (orientation === "landscape") [w, h] = [h, w];
  return {
    width: Math.round((w - 2 * MARGIN_LR_MM) * PX_PER_MM),
    height: Math.max(Math.round((h - 2 * MARGIN_TB_MM) * PX_PER_MM), 600),
  };
}

/**
 * Set the printed page geometry via an injected ``@page`` rule, and publish the
 * printable area as CSS vars. The vars let the print stylesheet size the content
 * column (and the cover) to the *final page* — so ECharts canvases render at the
 * page width instead of the user's window width (which Chromium would otherwise
 * rescale to the paper, making chart legibility depend on window size). The
 * server engine sizes its headless viewport to the paper instead, so it leaves
 * the vars unset and the CSS falls back to 100% / 100vh.
 */
function setPageRule(orientation, format) {
  let style = document.getElementById("dashdown-print-page");
  if (!style) {
    style = document.createElement("style");
    style.id = "dashdown-print-page";
    document.head.appendChild(style);
  }
  style.textContent = `@page { size: ${format || "A4"} ${orientation || "portrait"}; margin: ${MARGIN_TB_MM}mm ${MARGIN_LR_MM}mm; }`;
  const { width, height } = printableArea(orientation, format);
  const root = document.documentElement.style;
  root.setProperty("--dashdown-print-width", `${width}px`);
  root.setProperty("--dashdown-print-height", `${height}px`);
}

const PDF_FIELDS = [
  {
    name: "orientation",
    label: "Orientation",
    type: "select",
    default: "portrait",
    options: [
      { value: "portrait", label: "Portrait" },
      { value: "landscape", label: "Landscape" },
    ],
  },
  {
    name: "format",
    label: "Page size",
    type: "select",
    default: "A4",
    options: [
      { value: "A4", label: "A4" },
      { value: "Letter", label: "Letter" },
      { value: "Legal", label: "Legal" },
      { value: "A3", label: "A3" },
    ],
  },
];

let _printing = false;

/**
 * Header "Export PDF": pick orientation/page size, then export. On the live
 * server we hit `/_dashdown/api/pdf`, which renders this page with the **same**
 * headless Chromium engine as `dashdown pdf` — so the output matches the CLI,
 * not the browser's print dialog. In a static export (no server) we fall back to
 * the browser's own `window.print()` over the print stylesheet.
 */
async function onPdfExport(btn) {
  if (_printing) return; // guard against a double trigger (re-click / re-entry)
  _printing = true;
  try {
    const settings = await openExportModal({
      title: "Export PDF",
      submitLabel: "Export",
      fields: PDF_FIELDS,
    });
    if (!settings) return; // cancelled

    const build = readBuildConfig();
    if (build && build.static) {
      await clientPrint(settings); // no server in a static export
    } else {
      await serverExport(settings, btn);
    }
  } finally {
    _printing = false;
  }
}

/** Server-side export via headless Chromium (same engine as the CLI). */
async function serverExport(settings, btn) {
  const params = new URLSearchParams();
  // Forward the current filter state (non-`_` params) so the PDF matches the
  // page the author is looking at; the server loads that exact filtered URL.
  for (const [k, v] of new URLSearchParams(window.location.search)) {
    if (!k.startsWith("_")) params.append(k, v);
  }
  params.set("_path", window.location.pathname);
  params.set("_orientation", settings.orientation);
  params.set("_format", settings.format);

  const saved = btn ? btn.innerHTML : null;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Exporting…";
  }
  try {
    const res = await fetch(`/_dashdown/api/pdf?${params.toString()}`);
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        detail = (await res.json()).detail || detail;
      } catch (e) {
        /* non-JSON error body */
      }
      throw new Error(detail);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    triggerDownload(blob, (m && m[1]) || "page.pdf");
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = saved;
    }
  } catch (err) {
    console.error("PDF export failed:", err);
    if (btn) {
      btn.textContent = "Export failed";
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = saved;
      }, 2500);
    }
  }
}

/** Trigger a browser download of a Blob. */
function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Client-side fallback (static exports only): dress the page for print, let
 * ECharts resize into the new layout, then `window.print()` (the browser's
 * "Save as PDF"). Restores the interactive view afterwards.
 */
async function clientPrint(settings) {
  const prevTheme = document.documentElement.getAttribute("data-theme");
  const scrollY = window.scrollY;
  // Set the print flag BEFORE entering print mode: forcing the light theme
  // re-renders every chart, and forPrint() (chart.js) reads this flag to skip
  // the entry animation so the canvas is final when we print.
  window.__dashdownPrint = true;
  enterPrintMode();
  setPageRule(settings.orientation, settings.format);
  // The print layout changes container widths (vertical grid, page-width column),
  // so nudge ECharts to redraw its canvases at the new size before printing.
  window.dispatchEvent(new Event("resize"));

  let cleanedUp = false;
  const mql = window.matchMedia ? window.matchMedia("print") : null;
  const onMediaChange = (e) => {
    if (!e.matches) cleanup();
  };
  const cleanup = () => {
    if (cleanedUp) return;
    cleanedUp = true;
    window.removeEventListener("afterprint", cleanup);
    if (mql && mql.removeEventListener) mql.removeEventListener("change", onMediaChange);
    window.__dashdownPrint = false;
    leavePrintMode(prevTheme, scrollY);
    window.dispatchEvent(new Event("resize"));
  };
  // Restore as soon as printing ends. `afterprint` is reliable across modern
  // desktop browsers (where this button is shown); the `matchMedia('print')`
  // change is a redundant backstop. We deliberately DON'T blind-revert on a
  // short timer: window.print() blocks on desktop, so a fixed timeout could fire
  // while the user is still in the save dialog and flip the page (and the PDF)
  // back to the interactive layout mid-export.
  window.addEventListener("afterprint", cleanup);
  if (mql && mql.addEventListener) mql.addEventListener("change", onMediaChange);

  // Let the theme re-render + reflow settle so the first paint is final.
  await new Promise((r) => setTimeout(r, 400));
  window.print();
}

/** Wire the header "Export PDF" button. No-op when the button isn't present. */
export function initPdfButton() {
  const btn = document.getElementById("dashdown-pdf-btn");
  if (!btn) return;
  btn.addEventListener("click", () => onPdfExport(btn));
}

/**
 * Arm the readiness handshake: flip `window.__dashdownPrintReady` to true once
 * every (non-live) data component's first load has fired AND every chart canvas
 * exists, after a short settle. Time-boxed so a stuck/erroring/live component
 * can't hang the export forever. Shared by print export and screenshot capture —
 * ONE readiness signal, two entry points, so we never invent a second contract.
 */
function armReady() {
  window[READY_FLAG] = false;

  const pending = expectedQueries();
  document.addEventListener("dashdown:data-loaded", (e) => {
    const q = e.detail && e.detail.queryName;
    if (q) pending.delete(q);
  });

  const start = Date.now();
  const tick = () => {
    if (window[READY_FLAG]) return;
    const dataDone = pending.size === 0 && chartsRendered();
    if (dataDone || Date.now() - start > MAX_WAIT_MS) {
      // One settle pass so the final (animation-free) paint is committed.
      setTimeout(() => {
        window[READY_FLAG] = true;
      }, SETTLE_MS);
      return;
    }
    setTimeout(tick, 120);
  };
  // Let components register + kick off their fetches first.
  setTimeout(tick, 150);
}

/**
 * Initialize the headless capture path. No-op unless the page was opened by the
 * `dashdown pdf` runner (window.__dashdownPrint / ?_print) or the `dashdown
 * screenshot` runner (window.__dashdownCapture). Called from app.js::init()
 * outside the async-component gate, so a docs page with only prose/diagrams still
 * prints (and still signals ready). Print mode dresses the page for the PDF deck;
 * capture mode leaves the interactive view intact — both arm the same signal.
 */
export function initPrint() {
  const print = isPrint();
  const capture = !print && isCapture();
  if (!print && !capture) return;

  if (print) {
    window.__dashdownPrint = true;
    enterPrintMode();
  }
  armReady();
}
