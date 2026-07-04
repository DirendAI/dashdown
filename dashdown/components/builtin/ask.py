"""Ask component — authored LLM commentary on a query's data."""
from __future__ import annotations

import re

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.llm import DEFAULT_ANSWER_TTL, DEFAULT_MAX_ROWS, register_ask_def
from dashdown.components.builtin._util import (
    attr_bool,
    attr_int,
    attr_str,
    esc,
    grid_span_style,
    new_id,
    resolve_semantic,
    safe_json,
)
from dashdown.render.attrs import DataRef

# Inner content arrives as rendered HTML (e.g. wrapped in <p>); the prompt is
# the text, so strip tags and collapse whitespace.
_TAG_RE = re.compile(r"<[^>]+>")


def _inner_text(inner: str | None) -> str:
    if not inner:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", inner)).strip()


# font_size= presets follow the Tailwind text scale the rest of the UI speaks
# ("sm" is the stylesheet default, so setting it is a deliberate no-op). Not
# emitted as Tailwind classes — the prebuilt vendor/tailwind.css only contains
# classes the framework already uses, so a generated `text-*` would silently
# miss; an inline style always applies. The answer's inner typography is
# em-based (dashdown.css), so everything scales with this one value.
_FONT_SIZE_PRESETS = {
    "xs": "0.75rem",
    "sm": "0.875rem",
    "base": "1rem",
    "lg": "1.125rem",
}
# An explicit value lands inside a style attribute, so only a bare
# number + unit may pass through — nothing else can terminate the
# declaration or the attribute.
_FONT_SIZE_RE = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)(?:rem|em|px|%)", re.IGNORECASE)


def _font_size_value(raw: str | None) -> str | None:
    """Map a font_size attr to a safe CSS value, or None to fall back to the
    stylesheet default (a junk value never errors the card — same forgiveness
    as the replay attr)."""
    if not raw:
        return None
    raw = raw.strip()
    preset = _FONT_SIZE_PRESETS.get(raw.lower())
    if preset:
        return preset
    if _FONT_SIZE_RE.fullmatch(raw):
        return raw
    return None


@register_component("Ask")
class Ask(Component):
    """Pin a question to a query's result and render the LLM's answer as a
    prose block alongside the charts.

    Usage:
        <Ask data={top_products} ask="Which are the top 3 products and why?" />
        <Ask data={top_products}>Which are the top 3 products and why?</Ask>
        <Ask metric={sales.revenue} by={sales.region} ask="Which region leads?" />

    The data source is either a named query (`data={query}` — inline `:::query`,
    a shared SQL/Python library query) or a **semantic metric reference**
    (`metric={model.metric} by={model.dim}`) — the latter resolves to
    the *same* synthetic query a chart with those attrs builds, so <Ask> comments
    on semantic-layer data and shares the registered spec + result cache with any
    chart using the same metric/by.

    The prompt is registered server-side at render time (see dashdown/llm.py);
    the placeholder carries only an opaque id, so the public ask endpoint can't
    be fed arbitrary prompts. Requires an `llm:` block in dashdown.yaml.

    Optional attributes:
        label="..."      optional heading next to the AI badge. By default the
                         header is just a small sparkle icon (tooltip
                         "AI-generated commentary") — provenance stays visible
                         without an uppercase banner on every card.
        max_rows=50      rows of query data sent to the model
        cache_ttl=3600   seconds the answer stays cached server-side
                         (same spelling as cache_ttl on :::query blocks)
        font_size="sm"   answer text size: xs | sm (default) | base | lg, or
                         an explicit CSS length like "0.8rem" (strictly
                         number + rem/em/px/% — it lands in a style attribute).
                         The whole answer surface (headings, tables, code)
                         scales with it; junk values fall back to the default.
        replay="once"    typewriter replay of a cached/baked answer: "once"
                         (default — once per session per answer), "always",
                         or "off". A true cache miss always streams live;
                         prefers-reduced-motion always skips the effect.
        refresh=true     the viewer-facing ↻ regenerate button. `refresh=false`
                         removes it AND makes the endpoint ignore `_refresh=1`
                         for this ask — a regeneration is a billable LLM call,
                         so the opt-out is enforced server-side.
        inline           chrome-less: no card border/background — the answer
                         reads as part of the page prose (blog style). The
                         small ✦ AI badge stays visible so generated text is
                         always marked; the ↻ button and model attribution
                         appear on hover.
        highlight="a,b"  hover provenance: while the ask is hovered, page
                         elements bound to these queries (charts, tables, …)
                         glow amber. Defaults to the ask's own data query;
                         `highlight=false` disables. (Named `highlight`, not
                         `ref` — the query library's dbt-style ref() is a
                         different concept.)
        lazy=true        generate only once the card scrolls into view — an
                         unseen ask costs nothing, and the viewer watches the
                         answer type in. `lazy=false` loads on page load;
                         print/screenshot runs always load eagerly.
    """

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        # A `metric={model.metric}` reference resolves to the same synthetic
        # semantic query a chart would build (and records it on ctx.semantic_refs,
        # so the pipeline compiles + registers it in `_python_def_cache` exactly
        # like a chart's). Falls back to the `data={query}` path when absent.
        sem = resolve_semantic(attrs, ctx)
        if sem is not None:
            name = sem["query_name"]
            connector = ctx.semantic_refs[name].connector
        else:
            data_val = attrs.get("data")
            if isinstance(data_val, DataRef):
                name = data_val.name
            else:
                name = attr_str(attrs, "data")
            if not name:
                return (
                    '<div class="text-error">Ask requires data={query_name} '
                    "or metric={model.metric}</div>"
                )
            connector = ctx.query_connectors.get(name, ctx.default_connector)

        prompt = attr_str(attrs, "ask") or _inner_text(inner)
        if not prompt:
            return (
                '<div class="text-error">Ask requires an `ask="..."` attribute '
                "or inner text as the prompt</div>"
            )

        max_rows = max(1, attr_int(attrs, "max_rows", DEFAULT_MAX_ROWS))
        cache_ttl = max(0, attr_int(attrs, "cache_ttl", DEFAULT_ANSWER_TTL))
        allow_refresh = attr_bool(attrs, "refresh", True)
        inline = attr_bool(attrs, "inline", False)
        lazy = attr_bool(attrs, "lazy", True)

        # Provenance highlight: which query names glow while the ask is
        # hovered (ask.js matches their data-query-name nodes). Defaults to
        # the ask's own data query; highlight="a,b" points elsewhere;
        # highlight=false turns it off.
        highlight_val = attrs.get("highlight")
        if highlight_val is False:
            highlight_queries: list[str] = []
        elif highlight_val is None:
            highlight_queries = [name]
        elif isinstance(highlight_val, DataRef):
            highlight_queries = [highlight_val.name]
        else:
            highlight_queries = [
                h.strip() for h in str(highlight_val).split(",") if h.strip()
            ]
        ask = register_ask_def(
            name,
            connector,
            prompt,
            max_rows=max_rows,
            cache_ttl=cache_ttl,
            allow_refresh=allow_refresh,
            # Page frontmatter joins the prompt (and the id hash), so the model
            # knows which dashboard it's commenting on.
            page_title=ctx.page_title,
            page_description=ctx.page_description,
        )
        ctx.ask_defs.append(ask)

        # Replay policy for cached/baked answers (ask.js). Bare booleans map to
        # the obvious modes (`replay=false` → off); anything unknown falls back
        # to the default rather than erroring the card.
        replay = attrs.get("replay", "once")
        if isinstance(replay, bool):
            replay = "always" if replay else "off"
        replay = str(replay).lower()
        if replay not in ("once", "always", "off"):
            replay = "once"

        # Answer text size — presentation only, so (like inline/replay) it
        # stays out of the ask-id hash: restyling must never bust the answer
        # cache. Lands on the body div, which ask.js mutates but never
        # replaces, so the style survives loading/stream/replay/final states.
        font_size = _font_size_value(attr_str(attrs, "font_size"))
        body_style = f' style="font-size:{font_size}"' if font_size else ""

        label = attr_str(attrs, "label")
        cid = new_id("dashdown-ask")
        config_json = esc(
            safe_json(
                {
                    "ask_id": ask.id,
                    "query_name": name,
                    "replay": replay,
                    "highlight_queries": highlight_queries,
                    "lazy": lazy,
                }
            )
        )
        span = grid_span_style(attrs)
        style_attr = f' style="{span}"' if span else ""

        # The ↻ button is emitted only when the author allows regeneration —
        # ask.js null-guards its absence, and the endpoint enforces the same
        # opt-out server-side (see register_ask_def's allow_refresh).
        refresh_btn = (
            '<button type="button" class="dashdown-ask-refresh" hidden '
            'aria-label="Regenerate commentary" title="Regenerate">'
            '<svg fill="none" stroke="currentColor" stroke-width="2" '
            'viewBox="0 0 24 24" aria-hidden="true">'
            '<path stroke-linecap="round" stroke-linejoin="round" '
            'd="M4 4v6h6M20 20v-6h-6M5.5 10a7 7 0 0 1 12-3.5L20 9M4 15l2.5 2.5A7 7 0 0 0 18.5 14"/>'
            "</svg></button>"
            if allow_refresh
            else ""
        )

        # Header left: a small sparkle badge marking the block as AI-generated
        # (native title tooltip carries the detail on hover), plus the author's
        # optional label text. Provenance without an uppercase banner.
        label_html = (
            f'<span class="dashdown-ask-label text-xs font-medium uppercase '
            f'tracking-wide text-base-content/60">{esc(label)}</span>'
            if label
            else ""
        )
        # The model attribution lives inside the badge, revealed while the
        # card is hovered (CSS — see the .dashdown-ask:hover rules); ask.js
        # fills it from the answer payload's `model` field and drops `hidden`
        # once an answer arrives.
        badge = (
            '<span class="dashdown-ask-badge" title="AI-generated commentary">'
            '<svg fill="none" stroke="currentColor" stroke-width="1.5" '
            'viewBox="0 0 24 24" role="img" aria-label="AI-generated commentary">'
            '<path stroke-linejoin="round" '
            'd="M12 4.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1-5.1-1.9 5.1-1.9 1.9-5.1z"/>'
            '<path stroke-linejoin="round" '
            'd="M18.8 3.2l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"/>'
            "</svg>"
            # aria-hidden: the svg's aria-label already says "AI-generated
            # commentary" — without it a screen reader announces "AI" twice.
            '<span class="dashdown-ask-badge-text" aria-hidden="true">AI</span>'
            f"{label_html}"
            '<span class="dashdown-ask-model" hidden></span></span>'
        )

        # `inline` swaps the card chrome for a bare prose block; the hover
        # reveal of badge/refresh is CSS (.dashdown-ask-inline rules).
        container_class = (
            "dashdown-ask dashdown-ask-inline"
            if inline
            else "dashdown-ask card bg-base-100 border border-base-300 p-4"
        )

        # Loading state: a lone blinking terminal cursor (.dashdown-ask-cursor
        # in dashdown.css) — the answer types in like terminal output, so the
        # wait state speaks the same language. The refresh button stays hidden
        # until ask.js confirms a live server.
        return (
            f'<div id="{cid}"{style_attr} '
            f'data-async-component="ask" '
            f'data-config="{config_json}" '
            f'data-query-name="{esc(name)}" '
            f'class="{container_class}">'
            f'<div class="flex items-center justify-between gap-2">'
            f"{badge}"
            f"{refresh_btn}"
            f"</div>"
            f'<div class="dashdown-ask-body"{body_style}>'
            f'<span class="dashdown-ask-cursor"></span></div>'
            f"</div>"
        )
