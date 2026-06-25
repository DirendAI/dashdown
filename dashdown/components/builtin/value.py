"""Value component for displaying single query results in text."""
from __future__ import annotations

from typing import Any

from dashdown.components.base import Component, RenderContext, register_component
from dashdown.components.builtin._util import (
    attr_str,
    esc,
    format_config,
    new_id,
    resolve_dataset,
    resolve_semantic,
    safe_json,
)
from dashdown.render.attrs import DataRef


@register_component("Value")
class Value(Component):
    """Display a single value from a query result.

    Usage:
        <Value data={query_name} row=0 column="column_name" />
        <Value data={query_name} row=0 index=0 />
        <Value data={query_name} format="currency" currency="$" decimals=2 />
        <Value data={query_name} format="currency" currency="EUR" locale="de-DE" />
        <Value data={query_name} prefix="$" suffix=" USD" />
        <Value metric={sales.revenue} />   # a semantic metric (single scalar)
    """

    def render(
        self, attrs, ctx: RenderContext, inner: str | None = None
    ) -> str:
        # Semantic metric: `<Value metric={sales.revenue} />` renders the scalar
        # aggregate (no `by` -> one row), the metric name as the column. Its
        # measure format hint applies unless the author set their own.
        sem = resolve_semantic(attrs, ctx)
        if sem is not None:
            name = sem["query_name"]
            column = sem["metric"]
            row, index = 0, None
            prefix = attr_str(attrs, "prefix", "")
            suffix = attr_str(attrs, "suffix", "")
            cid = new_id("dashdown-value")
            config = {"query_name": name, "row": row, "prefix": prefix, "suffix": suffix, "column": column}
            if sem.get("format"):
                for k, v in sem["format"].items():
                    config.setdefault(k, v)
            config.update(format_config(attrs))
            config_json = esc(safe_json(config))
            return (
                f'<span id="{cid}" data-async-component="value" '
                f'data-config="{config_json}" data-query-name="{esc(name)}">'
                f'<span class="skeleton inline-block w-16 h-4"></span></span>'
            )

        data_val = attrs.get("data")
        if isinstance(data_val, DataRef):
            name = data_val.name
        else:
            name = attr_str(attrs, "data")

        if not name:
            return '<span class="text-error">Value requires data={query_name}</span>'

        # For async loading, we need the query name
        row = int(attr_str(attrs, "row", "0"))
        column = attr_str(attrs, "column")
        index = attr_str(attrs, "index")
        prefix = attr_str(attrs, "prefix", "")
        suffix = attr_str(attrs, "suffix", "")

        cid = new_id("dashdown-value")

        config = {
            "query_name": name,
            "row": row,
            "prefix": prefix,
            "suffix": suffix,
        }
        if column:
            config["column"] = column
        if index:
            config["index"] = int(index)
        # Display formatting (format/currency/decimals) — consumed by value.js.
        config.update(format_config(attrs))

        config_json = esc(safe_json(config))
        
        # Return a span that will be populated by JavaScript
        return (
            f'<span id="{cid}" '
            f'data-async-component="value" '
            f'data-config="{config_json}" '
            f'data-query-name="{esc(name)}">'
            f'<span class="skeleton inline-block w-16 h-4"></span>'
            f'</span>'
        )
