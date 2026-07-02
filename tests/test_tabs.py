"""Tests for the Tabs/Tab layout components."""
import json

import pytest

import dashdown.components  # noqa: F401  (registers built-ins)
from dashdown.components.base import RenderContext, get_component
from dashdown.render.components import render_components


@pytest.fixture
def ctx():
    return RenderContext(queries={}, params={}, current_path="/")


TWO_TABS = (
    '<Tabs><Tab title="Overview">One</Tab>'
    '<Tab title="By region">Two</Tab></Tabs>'
)


def _config(html: str) -> dict:
    """Extract the data-config JSON from rendered Tabs HTML."""
    import re
    m = re.search(r'data-config="([^"]*)"', html)
    assert m, "no data-config found"
    return json.loads(m.group(1).replace("&quot;", '"').replace("&amp;", "&"))


def test_tabs_and_tab_registered():
    assert get_component("Tabs") is not None
    assert get_component("Tab") is not None


def test_tabs_not_filters():
    assert get_component("Tabs").is_filter is False
    assert get_component("Tab").is_filter is False


def test_tabs_renders_structure(ctx):
    html = render_components(TWO_TABS, ctx)
    assert 'data-async-component="tabs"' in html
    assert 'class="dashdown-tabs"' in html
    assert 'role="tablist"' in html
    assert "dashdown-tabs-nav" in html
    assert "dashdown-tabs-panels" in html


def test_tab_panels_carry_titles_and_content(ctx):
    html = render_components(TWO_TABS, ctx)
    assert 'data-tab-title="Overview"' in html
    assert 'data-tab-title="By region"' in html
    assert html.count('class="dashdown-tab-panel"') == 2
    assert ">One</div>" in html
    assert ">Two</div>" in html


def test_tab_emits_print_heading(ctx):
    html = render_components(TWO_TABS, ctx)
    assert '<div class="dashdown-tab-panel-heading">Overview</div>' in html


def test_tab_title_is_escaped(ctx):
    # `<`/`>` inside attribute values are a pre-existing limitation of the
    # component tag regexes ([^>]*); quote/ampersand escaping is what Tab owns —
    # a quote in a title must not break out of the data-tab-title attribute.
    html = render_components(
        "<Tabs><Tab title=\"It's A&B\">Body</Tab></Tabs>", ctx
    )
    assert 'data-tab-title="It&#x27;s A&amp;B"' in html


def test_tab_requires_title(ctx):
    html = render_components("<Tabs><Tab>Body</Tab></Tabs>", ctx)
    assert "dashdown-error" in html
    assert "title" in html


def test_tabs_requires_tab_children(ctx):
    html = render_components("<Tabs>just text</Tabs>", ctx)
    assert "dashdown-error" in html


def test_tabs_config_defaults(ctx):
    config = _config(render_components(TWO_TABS, ctx))
    assert config == {"name": "", "default": "", "url_sync": True}


def test_tabs_config_carries_attrs(ctx):
    html = render_components(
        '<Tabs name="view" default="By region" url_sync=false>'
        '<Tab title="Overview">One</Tab>'
        '<Tab title="By region">Two</Tab></Tabs>',
        ctx,
    )
    config = _config(html)
    assert config["name"] == "view"
    assert config["default"] == "By region"
    assert config["url_sync"] is False


def test_tabs_renders_nested_components(ctx):
    html = render_components(
        '<Tabs><Tab title="Trend">'
        '<LineChart data={sales} x="m" y="v" />'
        "</Tab></Tabs>",
        ctx,
    )
    assert 'data-async-component="chart"' in html


def test_nested_tabs_keep_own_wrapper(ctx):
    html = render_components(
        '<Tabs><Tab title="Outer">'
        '<Tabs><Tab title="Inner">Deep</Tab></Tabs>'
        "</Tab></Tabs>",
        ctx,
    )
    assert html.count('data-async-component="tabs"') == 2
    assert 'data-tab-title="Inner"' in html


def test_tabs_survive_static_build():
    ctx = RenderContext(queries={}, params={}, current_path="/", static_build=True)
    html = render_components(TWO_TABS, ctx)
    assert 'data-async-component="tabs"' in html
    assert 'data-tab-title="Overview"' in html


def test_tabs_col_span(ctx):
    html = render_components(
        '<Tabs col-span=2><Tab title="A">One</Tab></Tabs>', ctx
    )
    assert "grid-column:span 2;" in html


def test_tabs_records_data_refs(ctx):
    render_components(
        '<Tabs><Tab title="Trend"><Table data={orders} /></Tab></Tabs>', ctx
    )
    assert "orders" in ctx.referenced_queries
