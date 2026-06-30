// Copy-to-clipboard buttons for fenced code blocks.
//
// The server-side highlighter (render/markdown.py::highlight_code) emits every
// fenced block as a static <pre class="dashdown-code" data-lang="…"><code>…</code></pre>
// shell — no client JS required to read it. This module is a pure progressive
// enhancement on top of that: it finds those blocks and adds a small "copy"
// button, the same way mermaid.js upgrades ```mermaid blocks. Because it rides
// in dashdown.js, it works on the dev server, in `dashdown build` static
// exports, and in chrome-less embeds with no extra wiring.
//
// Design notes:
//   - **Mermaid blocks are skipped** (`.dashdown-mermaid`): those <pre>s are
//     replaced by an SVG diagram by mermaid.js, so a copy button would be wrong
//     (and would copy the diagram source, not anything useful).
//   - **The button lives in a wrapper, not inside the <pre>.** The code block is
//     `overflow-x: auto`; an absolutely-positioned child of the <pre> would
//     scroll away with the code. So each block is wrapped in a non-scrolling
//     `.dashdown-code-wrap` and the button is pinned to that.
//   - Clipboard write mirrors embed_ui.js: the async Clipboard API with a
//     `document.execCommand("copy")` fallback for contexts where it's blocked.

"use strict";

// Feather-style icons (stroke=currentColor), matching the table export button.
const COPY_ICON =
  '<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" ' +
  'viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="13" height="13" ' +
  'rx="2" ry="2"/><path stroke-linecap="round" stroke-linejoin="round" ' +
  'd="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const CHECK_ICON =
  '<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" ' +
  'viewBox="0 0 24 24" aria-hidden="true"><path stroke-linecap="round" ' +
  'stroke-linejoin="round" d="M20 6L9 17l-5-5"/></svg>';

const BLOCK_SELECTOR = ".dashdown-prose pre.dashdown-code:not(.dashdown-mermaid)";

/** Copy `text` to the clipboard, falling back to execCommand if the API is blocked. */
async function copyText(text, scratch) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    // Clipboard API unavailable (insecure context / permissions) — fall back to
    // a hidden textarea + execCommand, the same path embed_ui.js uses.
    try {
      scratch.value = text;
      scratch.select();
      const ok = document.execCommand && document.execCommand("copy");
      scratch.blur();
      return !!ok;
    } catch (e2) {
      return false;
    }
  }
}

/** Add a copy button to a single not-yet-upgraded <pre.dashdown-code>. */
function upgradeBlock(pre) {
  // Idempotency guard: skip a block already inside a wrapper (e.g. a defensive
  // re-init). Fresh page loads start clean, but this keeps the op repeatable.
  if (pre.parentElement && pre.parentElement.classList.contains("dashdown-code-wrap")) {
    return;
  }
  const code = pre.querySelector("code");
  if (!code) return;

  const wrap = document.createElement("div");
  wrap.className = "dashdown-code-wrap";
  pre.replaceWith(wrap);
  wrap.appendChild(pre);

  // Off-screen scratch field for the execCommand fallback (kept out of the
  // scrolling <pre> and never focusable by tab).
  const scratch = document.createElement("textarea");
  scratch.className = "dashdown-code-copy-scratch";
  scratch.setAttribute("aria-hidden", "true");
  scratch.tabIndex = -1;

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "dashdown-code-copy";
  btn.title = "Copy";
  btn.setAttribute("aria-label", "Copy code");
  btn.innerHTML = COPY_ICON;

  let resetTimer = null; // per-button, so one block's reset can't cancel another's
  btn.addEventListener("click", async () => {
    const ok = await copyText(code.textContent || "", scratch);
    clearTimeout(resetTimer);
    btn.classList.toggle("dashdown-copied", ok);
    btn.classList.toggle("dashdown-copy-failed", !ok);
    btn.innerHTML = ok ? CHECK_ICON : COPY_ICON;
    btn.title = ok ? "Copied!" : "Press ⌘/Ctrl+C to copy";
    btn.setAttribute("aria-label", ok ? "Copied" : "Copy failed");
    resetTimer = setTimeout(() => {
      btn.classList.remove("dashdown-copied", "dashdown-copy-failed");
      btn.innerHTML = COPY_ICON;
      btn.title = "Copy";
      btn.setAttribute("aria-label", "Copy code");
    }, 1600);
  });

  wrap.appendChild(btn);
  wrap.appendChild(scratch);
}

/**
 * Add a copy button to every fenced code block on the page. Self-gating: a page
 * with no code blocks does nothing. Safe to call independently of the
 * async-component path — fenced code is static HTML, so a prose-only docs page
 * with no charts/tables still gets copy buttons.
 */
export function initAllCopyCode() {
  document.querySelectorAll(BLOCK_SELECTOR).forEach(upgradeBlock);
}
