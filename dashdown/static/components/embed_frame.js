// Embed auto-resize reporter.
//
// When a page renders inside an <iframe> in embed mode (?_embed → the
// `dashdown-embed` class set pre-paint on <html> in page.html), the host can't
// know the content's height. This posts the document height to the parent
// window so the standalone embed.js loader (or any host listener) can size the
// iframe to fit — no inner scrollbar, no clipping.
//
// No-op unless actually embedded in a frame, so it costs nothing on the app
// itself. Mirrors the small-module style of the other components/*.js.

"use strict";

const MSG_TYPE = "dashdown:resize";

function isEmbedded() {
  return (
    window.parent &&
    window.parent !== window &&
    document.documentElement.classList.contains("dashdown-embed")
  );
}

let _lastHeight = -1;

function postHeight() {
  const body = document.body;
  const h = Math.ceil(
    Math.max(
      document.documentElement.scrollHeight,
      body ? body.scrollHeight : 0
    )
  );
  if (h === _lastHeight) return; // coalesce — ResizeObserver fires often
  _lastHeight = h;
  try {
    // Target origin "*": the payload is only a height. The host-side loader
    // validates event.origin + event.source before trusting it.
    window.parent.postMessage({ type: MSG_TYPE, height: h }, "*");
  } catch (e) {
    /* parent gone */
  }
}

export function initEmbedFrame() {
  if (!isEmbedded()) return;

  postHeight();
  window.addEventListener("load", postHeight); // fonts/images settle height

  // Any layout change — charts drawing, data landing, filters toggling — should
  // re-report. ResizeObserver on <body> is the catch-all; the explicit events
  // below cover browsers/edge cases where the observer is slow to fire.
  if (typeof ResizeObserver !== "undefined" && document.body) {
    new ResizeObserver(postHeight).observe(document.body);
  }
  document.addEventListener("dashdown:data-loaded", postHeight);
  window.addEventListener("resize", postHeight);
}
