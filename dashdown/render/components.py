"""Scan rendered HTML for component tags and substitute their output."""
from __future__ import annotations

import re
import traceback

from dashdown.components.base import RenderContext, get_component, known_components
from dashdown.render.attrs import DataRef, parse_attrs


def _record_refs(attrs: dict, ctx: RenderContext) -> None:
    """Record every ``data={name}`` (DataRef) attribute into
    ``ctx.referenced_queries`` so the pipeline can resolve referenced shared-
    library queries after the component scan."""
    for value in attrs.values():
        if isinstance(value, DataRef):
            ctx.referenced_queries.add(value.name)

# Pattern to match component tag names (uppercase start)
_COMPONENT_NAME_RE = re.compile(r"[A-Z][A-Za-z0-9_]*")

# Pattern to match self-closing tags like <Tag ... />
_SELF_CLOSING_RE = re.compile(
    r"<([A-Z][A-Za-z0-9_]*)\s*([^>]*)/\s*>",
    re.DOTALL,
)

# Pattern to match opening tags like <Tag ...>
_OPENING_RE = re.compile(
    r"<([A-Z][A-Za-z0-9_]*)\s*([^>]*)>",
    re.DOTALL,
)

# Pattern to match closing tags like </Tag>
_CLOSING_RE = re.compile(
    r"</([A-Z][A-Za-z0-9_]*)\s*>",
    re.DOTALL,
)


def render_components(html: str, ctx: RenderContext, _depth: int = 0) -> str:
    """Render component tags in HTML recursively.
    
    This function scans for component tags and replaces them with their rendered
    output. It uses a stack-based approach to correctly handle nested components
    and sibling components of the same type.
    
    The algorithm:
    1. First, replace all self-closing tags (<Tag ... />) as they have no children
    2. Then, use a stack to match opening and closing tags for paired components
    3. When a closing tag is found that matches the top of the stack, render the component
    4. Recursively process the inner content between the opening and closing tags
    """
    if _depth > 10:  # Prevent infinite recursion
        return _error_card(
            "Max recursion depth exceeded",
            "Check for circular component references",
        )
    
    # Process self-closing tags first (they don't have children)
    def replace_self_closing(m: re.Match) -> str:
        name = m.group(1)
        attrs_str = m.group(2)
        comp = get_component(name)
        if comp is None:
            return _error_card(
                f"Unknown component &lt;{name}/&gt;",
                f"Known: {', '.join(known_components()) or '(none)'}",
            )
        # In a static export, filter controls can't re-query — omit them.
        if ctx.static_build and comp.is_filter:
            return ""
        if comp.is_filter:
            ctx.has_filters = True
        try:
            attrs = parse_attrs(" " + attrs_str if attrs_str else "")
            _record_refs(attrs, ctx)
            return comp.render(attrs, ctx, None)
        except Exception as e:
            return _error_card(
                f"Error rendering &lt;{name}/&gt;: {type(e).__name__}",
                str(e),
            )
    
    html = _SELF_CLOSING_RE.sub(replace_self_closing, html)
    
    # Now process paired tags using a stack-based character-by-character parser
    result: list[str] = []
    i = 0
    stack: list[tuple[str, str, int, list[str]]] = []  # (tag_name, attrs_str, start_pos, children)
    current_children: list[str] = []
    
    while i < len(html):
        # Check for opening tag
        opening_match = _OPENING_RE.match(html, i)
        if opening_match:
            tag_name = opening_match.group(1)
            attrs_str = opening_match.group(2)
            # Check if this is a component tag (starts with uppercase)
            if tag_name and tag_name[0].isupper():
                # Push current state to stack and start new children buffer
                stack.append((tag_name, attrs_str, i, current_children))
                current_children = []
                i = opening_match.end()
                continue
            else:
                # Not a component tag, add to current children
                current_children.append(html[i:opening_match.end()])
                i = opening_match.end()
                continue
        
        # Check for closing tag
        closing_match = _CLOSING_RE.match(html, i)
        if closing_match:
            closing_name = closing_match.group(1)
            if stack:
                opening_name, attrs_str, start_pos, parent_children = stack[-1]
                if opening_name == closing_name:
                    # Found matching pair - render this component
                    stack.pop()
                    # The inner content is everything in current_children
                    inner = "".join(current_children) + html[i:closing_match.start()]
                    
                    comp = get_component(opening_name)
                    if comp is None:
                        rendered = _error_card(
                            f"Unknown component &lt;{opening_name}/&gt;",
                            f"Known: {', '.join(known_components()) or '(none)'}",
                        )
                    elif ctx.static_build and comp.is_filter:
                        # In a static export, filter controls can't re-query — omit them.
                        rendered = ""
                    else:
                        if comp.is_filter:
                            ctx.has_filters = True
                        try:
                            attrs = parse_attrs(" " + attrs_str if attrs_str else "")
                            _record_refs(attrs, ctx)
                            # Recursively render inner content
                            inner_rendered = render_components(inner, ctx, _depth + 1)
                            rendered = comp.render(attrs, ctx, inner_rendered)
                        except Exception as e:
                            rendered = _error_card(
                                f"Error rendering &lt;{opening_name}/&gt;: {type(e).__name__}",
                                str(e),
                            )
                    
                    # The rendered component becomes a child of the parent
                    current_children = parent_children + [rendered]
                    i = closing_match.end()
                    continue
            # Not a matching closing tag or stack is empty
            current_children.append(html[i:closing_match.end()])
            i = closing_match.end()
            continue
        
        # No tag found, just copy the character to current children
        current_children.append(html[i])
        i += 1
    
    # Add any remaining children (from outermost level)
    result = current_children
    return "".join(result)


def _error_card(title: str, detail: str) -> str:
    return (
        '<div class="dashdown-error alert alert-error">'
        f'<div class="dashdown-error-title font-bold">{title}</div>'
        f'<pre class="dashdown-error-detail text-sm">{detail}</pre>'
        "</div>"
    )
