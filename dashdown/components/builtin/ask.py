"""Ask component — authored LLM commentary on a query's data."""
from __future__ import annotations

import re

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.llm import DEFAULT_ANSWER_TTL, DEFAULT_MAX_ROWS, register_ask_def
from dashdown.components.builtin._util import (
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
        label="..."      card header text (default "Commentary")
        max_rows=50      rows of query data sent to the model
        cache_ttl=3600   seconds the answer stays cached server-side
                         (same spelling as cache_ttl on :::query blocks)
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
        ask = register_ask_def(
            name, connector, prompt, max_rows=max_rows, cache_ttl=cache_ttl
        )
        ctx.ask_defs.append(ask)

        label = attr_str(attrs, "label", "Commentary")
        cid = new_id("dashdown-ask")
        config_json = esc(safe_json({"ask_id": ask.id, "query_name": name}))
        span = grid_span_style(attrs)
        style_attr = f' style="{span}"' if span else ""

        # Shimmer lines sized via dashdown.css (.dashdown-ask-skeleton); the
        # refresh button stays hidden until ask.js confirms a live server, and
        # the model attribution stays hidden until ask.js fills it from the
        # answer payload's `model` field.
        return (
            f'<div id="{cid}"{style_attr} '
            f'data-async-component="ask" '
            f'data-config="{config_json}" '
            f'data-query-name="{esc(name)}" '
            f'class="dashdown-ask card bg-base-100 border border-base-300 p-4">'
            f'<div class="flex items-center justify-between gap-2">'
            f'<div class="dashdown-ask-label text-xs font-medium uppercase tracking-wide text-base-content/60">{esc(label)}</div>'
            f'<button type="button" class="dashdown-ask-refresh" hidden '
            f'aria-label="Regenerate commentary" title="Regenerate">'
            f'<svg fill="none" stroke="currentColor" stroke-width="2" '
            f'viewBox="0 0 24 24" aria-hidden="true">'
            f'<path stroke-linecap="round" stroke-linejoin="round" '
            f'd="M4 4v6h6M20 20v-6h-6M5.5 10a7 7 0 0 1 12-3.5L20 9M4 15l2.5 2.5A7 7 0 0 0 18.5 14"/>'
            f"</svg></button>"
            f"</div>"
            f'<div class="dashdown-ask-body">'
            f'<div class="dashdown-ask-skeleton">'
            f'<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>'
            f"</div></div>"
            f'<div class="dashdown-ask-model" hidden></div>'
            f"</div>"
        )
