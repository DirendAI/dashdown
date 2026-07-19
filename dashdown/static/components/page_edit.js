// Dashdown source editor (dev server only)
//
// Turns a page into its own source: a header ✎ button opens a whole-page
// markdown editor, and every *kept* answer section (an ask answer the operator
// pressed "Keep on this page" on) gains a hover toolbar to edit or remove just
// that section. Both talk to the dev-server-only page-source API:
//   GET  /_dashdown/api/page-source?path=… -> {path, markdown, token}
//   PUT  /_dashdown/api/page-source         -> {ok, token} | 409 {detail, token}
// The `token` is a content fingerprint (sha1 of the file bytes), so a no-op
// save never conflicts and a concurrent disk edit surfaces as a 409.
//
// After a successful save the framework's file watcher live-reloads the page,
// so this module never reloads programmatically — it just closes the dialog.
//
// Gated by the `#dashdown-page-edit` config node the server emits only on the
// dev server (never in embeds / static builds), so the module returns
// immediately when that node is absent or disabled — this ships in the wheel
// but costs nothing off the dev server.

"use strict";

const _PAGE_SOURCE_URL = "/_dashdown/api/page-source";
// sessionStorage key the ask box writes after a successful keep, so the freshly
// appended section can be flashed once the reloaded page comes back up.
const _FLASH_KEY = "dashdown-keep-flash";

// Kept-section markers, read from the *rendered* page's comment nodes. A comment
// node's value excludes the surrounding `<!--`/`-->`, so these match the inner
// text only. Mirrors ask_engine.find_kept_sections' regexes (the single marker
// authority) — kept in sync with that writer/reader pair.
const _OPEN_COMMENT_RE = /^\s*dashdown:keep\s+id=([0-9a-f]{8})\s+kind=(\w+)/;
const _CLOSE_COMMENT_RE = /^\s*\/dashdown:keep\s+id=([0-9a-f]{8})\s*$/;

function prefersReducedMotion() {
  return (
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * Locate every well-formed kept section in a page's *source* markdown by its
 * marker pair. The client port of ask_engine.find_kept_sections: an opening
 * `<!-- dashdown:keep id=<8hex> kind=<kind> … -->` is paired with the next
 * matching `<!-- /dashdown:keep id=<same> -->`; the inclusive span between them
 * is one section. Unclosed / malformed markers are skipped. Returned in order.
 * @param {string} md
 * @returns {Array<{id: string, kind: string, start: number, end: number}>}
 */
function findKeptSections(md) {
  const openRe = /<!--\s*dashdown:keep\s+id=([0-9a-f]{8})\s+kind=(\w+).*?-->/g;
  const out = [];
  let m;
  while ((m = openRe.exec(md)) !== null) {
    const id = m[1];
    const kind = m[2];
    // id is 8 hex chars, so it's safe to interpolate into a RegExp.
    const closeRe = new RegExp("<!--\\s*/dashdown:keep\\s+id=" + id + "\\s*-->");
    const rest = md.slice(m.index + m[0].length);
    const cm = closeRe.exec(rest);
    if (!cm) continue; // unclosed — skip
    const end = m.index + m[0].length + cm.index + cm[0].length;
    out.push({ id, kind, start: m.index, end });
  }
  return out;
}

// ---- API client ----------------------------------------------------------

/** GET the page source. Resolves to {path, markdown, token}; throws on error. */
async function fetchSource(path) {
  const url = _PAGE_SOURCE_URL + "?path=" + encodeURIComponent(path);
  const r = await fetch(url);
  const data = await r.json().catch(() => null);
  if (!r.ok || !data) {
    throw new Error((data && data.detail) || `Load failed (HTTP ${r.status})`);
  }
  return data;
}

/**
 * PUT the page source with its read-time token. Resolves to {ok, token} on
 * success, {conflict: true, token} on a 409 (the file changed on disk), and
 * throws on any other error.
 */
async function putSource(path, markdown, token) {
  const r = await fetch(_PAGE_SOURCE_URL, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, markdown, token }),
  });
  const data = await r.json().catch(() => null);
  if (r.status === 409) return { conflict: true, token: data && data.token };
  if (!r.ok || !data || !data.ok) {
    throw new Error((data && data.detail) || `Save failed (HTTP ${r.status})`);
  }
  return { ok: true, token: data.token };
}

// ---- Toast ----------------------------------------------------------------

function showToast(message) {
  const toast = document.createElement("div");
  toast.className = "dashdown-page-edit-toast";
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.appendChild(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

// ---- Whole-page + section editor dialog ----------------------------------

/**
 * Open the source-editor dialog. `spec` decouples the layout from what's being
 * edited:
 *   ariaLabel  - the dialog's aria-label.
 *   pathLabel  - the subtle footer label (page path / section id).
 *   load()     - async () => {text, token}; the initial fill AND the conflict
 *                "load disk version" source. Throws to abort the open (toast).
 *   put(text)  - async (text) => {ok, token} | {conflict, token}; the write. A
 *                section editor handles its own read-modify-write and never
 *                returns a conflict; the page editor returns the 409 token so
 *                the dialog can offer disk-vs-mine.
 */
async function openEditor(spec) {
  let loaded;
  try {
    loaded = await spec.load();
  } catch (e) {
    showToast((e && e.message) || "Could not open the editor");
    return;
  }

  const dialog = document.createElement("dialog");
  dialog.className = "dashdown-page-editor";
  dialog.setAttribute("aria-label", spec.ariaLabel);

  const box = document.createElement("div");
  box.className = "dashdown-page-editor-box";

  const banner = document.createElement("div");
  banner.className = "dashdown-page-editor-conflict";
  banner.hidden = true;

  const textarea = document.createElement("textarea");
  textarea.className = "dashdown-page-editor-textarea";
  textarea.spellcheck = false;
  textarea.setAttribute("aria-label", spec.ariaLabel);
  textarea.value = loaded.text;

  const footer = document.createElement("div");
  footer.className = "dashdown-page-editor-footer";
  const pathLabel = document.createElement("span");
  pathLabel.className = "dashdown-page-editor-path";
  pathLabel.textContent = spec.pathLabel;
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.className = "dashdown-page-editor-cancel";
  cancelBtn.textContent = "Cancel";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "dashdown-page-editor-save";
  saveBtn.textContent = "Save";
  footer.appendChild(pathLabel);
  footer.appendChild(cancelBtn);
  footer.appendChild(saveBtn);

  box.appendChild(banner);
  box.appendChild(textarea);
  box.appendChild(footer);
  dialog.appendChild(box);
  document.body.appendChild(dialog);

  let token = loaded.token;
  let savedText = loaded.text; // last on-disk value (for the dirty check)
  const isDirty = () => textarea.value !== savedText;

  function clearBanner() {
    banner.hidden = true;
    banner.textContent = "";
  }

  // The 409 flow: keep the operator's text, offer disk-vs-mine with the fresh
  // token. Only the page editor reaches this (section edits self-reconcile).
  function showConflict(conflictToken) {
    banner.textContent = "";
    const msg = document.createElement("span");
    msg.className = "dashdown-page-editor-conflict-msg";
    msg.textContent = "Page changed on disk — reloading the editor.";
    const loadBtn = document.createElement("button");
    loadBtn.type = "button";
    loadBtn.className = "dashdown-page-editor-conflict-load";
    loadBtn.textContent = "Load disk version";
    const mineBtn = document.createElement("button");
    mineBtn.type = "button";
    mineBtn.className = "dashdown-page-editor-conflict-mine";
    mineBtn.textContent = "Overwrite with mine";
    banner.appendChild(msg);
    banner.appendChild(loadBtn);
    banner.appendChild(mineBtn);
    banner.hidden = false;

    loadBtn.addEventListener("click", async () => {
      try {
        const disk = await spec.load();
        textarea.value = disk.text;
        token = disk.token;
        savedText = disk.text;
        clearBanner();
        textarea.focus();
      } catch (e) {
        showToast((e && e.message) || "Could not load the disk version");
      }
    });
    mineBtn.addEventListener("click", () => doSave(conflictToken));
  }

  let saving = false;
  async function doSave(overrideToken) {
    if (saving) return;
    saving = true;
    saveBtn.disabled = true;
    clearBanner();
    try {
      const res = await spec.put(
        textarea.value,
        overrideToken != null ? overrideToken : token
      );
      if (res.conflict) {
        showConflict(res.token);
        return;
      }
      token = res.token;
      savedText = textarea.value;
      // The dev watcher live-reloads the page on the write; just close.
      dialog.close();
    } catch (e) {
      banner.textContent = (e && e.message) || "Save failed";
      banner.hidden = false;
    } finally {
      saving = false;
      saveBtn.disabled = false;
    }
  }

  function requestClose() {
    if (isDirty() && !window.confirm("Discard your changes?")) return;
    dialog.close();
  }

  saveBtn.addEventListener("click", () => doSave());
  cancelBtn.addEventListener("click", requestClose);
  // Cmd/Ctrl+S saves from anywhere in the dialog.
  dialog.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      doSave();
    }
  });
  // Escape fires the native `cancel` event before `close`; guard it when dirty.
  dialog.addEventListener("cancel", (e) => {
    if (isDirty() && !window.confirm("Discard your changes?")) {
      e.preventDefault();
    }
  });
  dialog.addEventListener("close", () => dialog.remove());
  // A click on the backdrop (the dialog fills the viewport, the box is centered)
  // requests close — same posture as the export modal.
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) requestClose();
  });

  dialog.showModal();
  textarea.focus();
}

// ---- Section read-modify-write -------------------------------------------

/**
 * Run a fresh read → mutate → write against the page source, retrying once on a
 * 409 (a disk edit landing in the read/write gap). `mutate(markdown)` returns
 * the new markdown or throws to abort (e.g. the section id vanished). Section
 * ops locate by id every time, so they tolerate the file having been edited
 * elsewhere on disk.
 * @param {string} path
 * @param {(md: string) => string} mutate
 */
async function readModifyWrite(path, mutate) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const fresh = await fetchSource(path);
    const newMd = mutate(fresh.markdown);
    const res = await putSource(path, newMd, fresh.token);
    if (!res.conflict) return res;
  }
  throw new Error("The page kept changing on disk — try again.");
}

const _SECTION_GONE = "This section is no longer in the page source.";

/** Open the editor scoped to a single kept section (by id). */
function openSectionEditor(path, id, heading) {
  openEditor({
    ariaLabel: "Edit kept section: " + heading,
    pathLabel: path + " · kept section",
    load: async () => {
      const fresh = await fetchSource(path);
      const sec = findKeptSections(fresh.markdown).find((s) => s.id === id);
      if (!sec) throw new Error(_SECTION_GONE);
      return { text: fresh.markdown.slice(sec.start, sec.end), token: fresh.token };
    },
    // The section editor reconciles by id on every save, so it never surfaces a
    // conflict to the dialog — it just splices the operator's text into the
    // current on-disk section span.
    put: async (text) => {
      return readModifyWrite(path, (md) => {
        const sec = findKeptSections(md).find((s) => s.id === id);
        if (!sec) throw new Error(_SECTION_GONE);
        return md.slice(0, sec.start) + text + md.slice(sec.end);
      });
    },
  });
}

/** Remove a kept section (by id) after a confirm, splicing it out of the file. */
async function removeSection(path, id, heading) {
  if (!window.confirm('Remove the kept section "' + heading + '"?')) return;
  try {
    await readModifyWrite(path, (md) => {
      const sec = findKeptSections(md).find((s) => s.id === id);
      if (!sec) throw new Error(_SECTION_GONE);
      const before = md.slice(0, sec.start);
      const after = md.slice(sec.end);
      // The section text is bracketed by blank lines (build_kept_markdown writes
      // "\n…\n"); collapse the seam so removal doesn't pile up blank lines.
      return (before.replace(/\n\s*$/, "\n") + after.replace(/^\s*\n/, "")).replace(
        /\n{3,}/g,
        "\n\n"
      );
    });
    // The dev watcher live-reloads on the write; nothing else to do.
  } catch (e) {
    showToast((e && e.message) || "Remove failed");
  }
}

// ---- Kept-section toolbars (rendered page) --------------------------------

/**
 * Wrap each kept section in the rendered page (open comment → close comment,
 * inclusive) with a hover/focus toolbar offering Edit + Remove. Returns a map of
 * keep id → wrapper element so the post-keep flash can find its section.
 * @param {string} path
 * @returns {Map<string, HTMLElement>}
 */
function decorateKeptSections(path) {
  const root = document.querySelector("main .dashdown-prose") || document.body;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_COMMENT);
  const opens = [];
  let node;
  while ((node = walker.nextNode())) {
    const m = _OPEN_COMMENT_RE.exec(node.nodeValue || "");
    if (m) opens.push({ comment: node, id: m[1], kind: m[2] });
  }

  const wrappers = new Map();
  for (const open of opens) {
    // Find the matching close comment among the open comment's siblings (block
    // HTML comments render as siblings of the heading/components between them).
    let close = null;
    for (let sib = open.comment.nextSibling; sib; sib = sib.nextSibling) {
      if (sib.nodeType === Node.COMMENT_NODE) {
        const cm = _CLOSE_COMMENT_RE.exec(sib.nodeValue || "");
        if (cm && cm[1] === open.id) {
          close = sib;
          break;
        }
      }
    }
    if (!close) continue; // unclosed in the DOM — skip

    // Collect the sibling run [open … close] before moving anything.
    const nodes = [];
    for (let sib = open.comment; sib; sib = sib.nextSibling) {
      nodes.push(sib);
      if (sib === close) break;
    }

    const wrapper = document.createElement("div");
    wrapper.className = "dashdown-kept-wrap";
    wrapper.dataset.keepId = open.id;
    open.comment.parentNode.insertBefore(wrapper, open.comment);
    nodes.forEach((n) => wrapper.appendChild(n));

    const headingEl = wrapper.querySelector("h1,h2,h3,h4,h5,h6");
    const heading = (headingEl && headingEl.textContent.trim()) || "kept answer";

    const toolbar = document.createElement("div");
    toolbar.className = "dashdown-kept-toolbar";
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "dashdown-kept-edit";
    editBtn.textContent = "✎ Edit";
    editBtn.setAttribute("aria-label", "Edit kept section: " + heading);
    editBtn.addEventListener("click", () =>
      openSectionEditor(path, open.id, heading)
    );
    const rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.className = "dashdown-kept-remove";
    rmBtn.textContent = "✕ Remove";
    rmBtn.setAttribute("aria-label", "Remove kept section: " + heading);
    rmBtn.addEventListener("click", () => removeSection(path, open.id, heading));
    toolbar.appendChild(editBtn);
    toolbar.appendChild(rmBtn);
    wrapper.insertBefore(toolbar, wrapper.firstChild);

    wrappers.set(open.id, wrapper);
  }
  return wrappers;
}

// ---- Post-keep flash ------------------------------------------------------

// After a keep + live-reload, briefly highlight the freshly appended section so
// the operator sees where their answer landed. The ask box wrote its id into
// sessionStorage before the reload.
function flashKeptSection(wrappers) {
  let flashId;
  try {
    flashId = window.sessionStorage.getItem(_FLASH_KEY);
  } catch (e) {
    return; // storage blocked — no flash, no error
  }
  if (!flashId) return;
  const wrapper = wrappers.get(flashId);
  try {
    window.sessionStorage.removeItem(_FLASH_KEY);
  } catch (e) {
    /* best-effort */
  }
  if (!wrapper) return;
  wrapper.scrollIntoView({
    behavior: prefersReducedMotion() ? "auto" : "smooth",
    block: "center",
  });
  wrapper.classList.add("dashdown-kept-flash");
  window.setTimeout(() => wrapper.classList.remove("dashdown-kept-flash"), 2000);
}

/**
 * Wire the source editor: the header ✎ (whole-page), a toolbar on every kept
 * section (edit / remove), and the post-keep flash. A no-op — returning before
 * touching anything — when the config node is absent or disabled, so static
 * builds and embeds (which never emit it) cost nothing.
 */
export function initPageEdit() {
  const node = document.getElementById("dashdown-page-edit");
  if (!node) return;
  let config = {};
  try {
    config = JSON.parse(node.dataset.config || "{}");
  } catch (e) {
    return;
  }
  if (!config.enabled || !config.path) return;
  const path = config.path;

  const editBtn = document.querySelector(".dashdown-page-edit-btn");
  if (editBtn) {
    editBtn.addEventListener("click", () =>
      openEditor({
        ariaLabel: "Edit page source",
        pathLabel: path,
        load: async () => {
          const data = await fetchSource(path);
          return { text: data.markdown, token: data.token };
        },
        put: async (text, token) => putSource(path, text, token),
      })
    );
  }

  const wrappers = decorateKeptSections(path);
  flashKeptSection(wrappers);
}
