// Embed snippet UI: the header "Embed" button.
//
// Builds a paste-ready <script> snippet for the *current* page and shows it in a
// small modal with a copy button. When the dashboard has auth + an embed.secret,
// it first mints a signed, page-scoped token from /_dashdown/api/embed-token
// (the author's browser is already authenticated). Without a secret it falls
// back to a token-free `_embed=1` snippet (works only for open dashboards).
//
// No-op unless `#dashdown-embed-btn` is present — the button is rendered only on
// the live server when embed.enabled, and never inside an embedded view.

"use strict";

import { esc } from "../core.js";

function buildSnippet(origin, path, token, theme) {
  const attrs = [
    `  src="${esc(origin)}/_dashdown/static/embed.js"`,
    `  data-dashdown-page="${esc(path)}"`,
  ];
  if (token) attrs.push(`  data-dashdown-token="${esc(token)}"`);
  if (theme) attrs.push(`  data-dashdown-theme="${esc(theme)}"`);
  return `<script\n${attrs.join("\n")}></scr` + `ipt>`;
}

let _modal = null;

function ensureModal() {
  if (_modal) return _modal;
  const overlay = document.createElement("div");
  overlay.className = "dashdown-embed-modal";
  overlay.hidden = true;
  overlay.innerHTML = `
    <div class="dashdown-embed-modal-panel" role="dialog" aria-modal="true"
         aria-label="Embed this page">
      <p class="dashdown-embed-modal-title">Embed this page</p>
      <p class="dashdown-embed-modal-hint"></p>
      <textarea class="dashdown-embed-modal-code" readonly spellcheck="false"></textarea>
      <div class="dashdown-embed-modal-actions">
        <button type="button" class="btn btn-ghost btn-sm" data-embed-close>Close</button>
        <button type="button" class="btn btn-primary btn-sm" data-embed-copy>Copy</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const close = () => {
    overlay.hidden = true;
  };
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close(); // backdrop click
  });
  overlay.querySelector("[data-embed-close]").addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.hidden) close();
  });

  const copyBtn = overlay.querySelector("[data-embed-copy]");
  copyBtn.addEventListener("click", async () => {
    const ta = overlay.querySelector(".dashdown-embed-modal-code");
    try {
      await navigator.clipboard.writeText(ta.value);
    } catch (e) {
      ta.select(); // clipboard API blocked — fall back to manual selection
      document.execCommand && document.execCommand("copy");
    }
    const prev = copyBtn.textContent;
    copyBtn.textContent = "Copied!";
    setTimeout(() => {
      copyBtn.textContent = prev;
    }, 1500);
  });

  _modal = overlay;
  return overlay;
}

function showModal(snippet, hint) {
  const overlay = ensureModal();
  overlay.querySelector(".dashdown-embed-modal-code").value = snippet;
  overlay.querySelector(".dashdown-embed-modal-hint").textContent = hint;
  overlay.hidden = false;
  overlay.querySelector(".dashdown-embed-modal-code").focus();
}

export function initEmbedUI() {
  const btn = document.getElementById("dashdown-embed-btn");
  if (!btn) return; // not the full-shell view, or embedding disabled

  btn.addEventListener("click", async () => {
    const origin = window.location.origin;
    const path = window.location.pathname || "/";

    let token = null;
    let hint =
      "Paste this into any page. It embeds this dashboard as an auto-resizing iframe.";
    try {
      const resp = await fetch(
        "/_dashdown/api/embed-token?path=" + encodeURIComponent(path)
      );
      if (resp.ok) {
        const data = await resp.json();
        token = data.token || null;
        if (token && data.exp) {
          hint =
            "Signed for this page" +
            (data.exp ? " — token expires " + new Date(data.exp * 1000).toLocaleString() : "") +
            ". Re-open this dialog to mint a fresh one.";
        }
      }
      // A 503 means no embed.secret (open dashboard) → token-free snippet below.
    } catch (e) {
      /* network error — fall back to a token-free snippet */
    }

    showModal(buildSnippet(origin, path, token, null), hint);
  });
}
