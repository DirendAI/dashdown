// Dashdown export-settings dialog.
//
// A tiny settings dialog shown before an export runs: the table CSV export
// (include-header / delimiter) and the header PDF export (orientation / page
// size) both call openExportModal() and act on the values it resolves with.
//
// Built on the native <dialog> element with DaisyUI's modal markup
// (.modal/.modal-box/.modal-action), so it gets a real focus trap, Escape-to-
// close, and a top-layer ::backdrop for free. Resolves null when cancelled.

"use strict";

import { esc } from "../core.js";

function fieldHtml(f) {
  if (f.type === "checkbox") {
    return (
      `<label class="dashdown-export-field flex items-center gap-2 cursor-pointer">` +
      `<input type="checkbox" name="${esc(f.name)}" class="checkbox"` +
      `${f.default ? " checked" : ""}>` +
      `<span>${esc(f.label)}</span></label>`
    );
  }
  if (f.type === "select") {
    const opts = (f.options || [])
      .map(
        (o) =>
          `<option value="${esc(String(o.value))}"` +
          `${o.value === f.default ? " selected" : ""}>${esc(o.label)}</option>`
      )
      .join("");
    return (
      `<label class="dashdown-export-field flex items-center justify-between gap-3">` +
      `<span>${esc(f.label)}</span>` +
      `<select name="${esc(f.name)}" class="select select-sm select-bordered">${opts}</select>` +
      `</label>`
    );
  }
  return "";
}

/**
 * Open a settings dialog and resolve with the chosen values (or null on cancel).
 *
 * @param {{title: string, submitLabel?: string, fields: Array<
 *   {name: string, label: string, type: "checkbox"|"select",
 *    default?: *, options?: Array<{value: *, label: string}>}>}} spec
 * @returns {Promise<Object|null>}
 */
export function openExportModal(spec) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "modal dashdown-export-modal";
    dialog.innerHTML =
      `<div class="modal-box">` +
      `<h3 class="text-lg font-semibold mb-4">${esc(spec.title)}</h3>` +
      `<div class="dashdown-export-modal-fields flex flex-col gap-3">` +
      spec.fields.map(fieldHtml).join("") +
      `</div>` +
      `<div class="modal-action">` +
      `<button type="button" class="btn btn-ghost btn-sm" data-action="cancel">Cancel</button>` +
      `<button type="button" class="btn btn-primary btn-sm" data-action="submit">` +
      `${esc(spec.submitLabel || "Export")}</button>` +
      `</div></div>`;

    let result = null;
    const collect = () => {
      const out = {};
      for (const f of spec.fields) {
        const node = dialog.querySelector(`[name="${f.name}"]`);
        if (!node) continue;
        out[f.name] = f.type === "checkbox" ? node.checked : node.value;
      }
      return out;
    };

    // The <dialog> "close" event is the single exit point — fired by Escape, a
    // backdrop click, or our buttons calling dialog.close().
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(result);
    });
    dialog.addEventListener("click", (e) => {
      // Click outside the box (the dialog fills the viewport) cancels.
      if (e.target === dialog) return dialog.close();
      const action = e.target.closest("[data-action]");
      if (!action) return;
      if (action.dataset.action === "submit") result = collect();
      dialog.close();
    });
    // Enter anywhere but a button submits.
    dialog.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target.tagName !== "BUTTON") {
        e.preventDefault();
        result = collect();
        dialog.close();
      }
    });

    document.body.appendChild(dialog);
    dialog.showModal();
  });
}
