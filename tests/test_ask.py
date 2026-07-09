"""Tests for the <Ask /> LLM commentary feature (Stage 11).

Layers: `llm:` config parsing, prompt registration via render_page, the
payload row cap, the /_dashdown/api/ask endpoint with a fake adapter
(including answer caching), and static-build baking.
"""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from dashdown.build import _build
from dashdown.chart_annotations import (
    ANNOTATION_VOCAB,
    MAX_ANNOTATIONS,
    ChartContext,
    annotation_instructions,
    build_chart_context,
    inject_refs,
    split_annotated_answer,
    strip_ref_tokens,
    validate_annotations,
)
from dashdown.data.base import QueryResult
from dashdown.llm import (
    DEFAULT_ANSWER_TTL,
    DEFAULT_EXPLAIN_MAX_ROWS,
    DEFAULT_MAX_ROWS,
    AnthropicAdapter,
    AskDef,
    LLMAdapter,
    LLMConfig,
    MistralAdapter,
    OpenAIAdapter,
    OpenRouterAdapter,
    _answer_cache,
    _ask_def_cache,
    ask_id,
    build_ask_prompt,
    cache_answer,
    create_adapter,
    format_result_for_llm,
    generate_answer,
    get_ask_def,
    get_cached_answer,
    known_providers,
    parse_llm_config,
    register_ask_def,
    relevant_params,
    resolve_model_name,
    stream_answer,
    unavailable_notice,
)
from dashdown.project import load_project
from dashdown.render import pipeline
from dashdown.render.markdown import render_markdown_text
from dashdown.render.pipeline import render_page
from dashdown.server import create_app


@pytest.fixture(autouse=True)
def _clear_caches():
    """Ask defs / answers / query results are module-global; isolate tests."""
    def _clear():
        _ask_def_cache.clear()
        _answer_cache.clear()
        pipeline._query_def_cache.clear()
        pipeline._result_cache.clear()
        # Python / semantic synthetic queries register in a parallel cache.
        pipeline._python_def_cache.clear()
        pipeline._stream_def_cache.clear()

    _clear()
    yield
    _clear()


class FakeAdapter(LLMAdapter):
    """Records every call; returns a canned markdown answer."""

    def __init__(self, reply: str = "**North** leads the pack."):
        super().__init__(LLMConfig(provider="mistral", api_key="test"))
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        return self.reply


class StreamingFakeAdapter(FakeAdapter):
    """Streams the reply in several chunks (native-streaming stand-in)."""

    def __init__(self, chunks=("**North** ", "leads ", "the pack.")):
        super().__init__(reply="".join(chunks))
        self.chunks = list(chunks)

    def stream_complete(self, system: str, prompt: str):
        self.calls.append((system, prompt))
        yield from self.chunks


# --------------------------------------------------------------------------- #
# parse_llm_config
# --------------------------------------------------------------------------- #
class TestParseLLMConfig:
    def test_none_when_missing(self):
        cfg = parse_llm_config(None)
        assert cfg.provider == "none"
        assert cfg.enabled is False

    def test_explicit_none(self):
        cfg = parse_llm_config({"provider": "none"})
        assert cfg.enabled is False

    def test_mistral_defaults(self):
        cfg = parse_llm_config({"provider": "mistral", "api_key": "sk-x"})
        assert cfg.enabled is True
        assert cfg.provider == "mistral"
        assert cfg.api_key == "sk-x"
        assert cfg.model is None

    def test_env_var_expansion(self, monkeypatch):
        monkeypatch.setenv("TEST_MISTRAL_KEY", "from-env")
        cfg = parse_llm_config(
            {"provider": "mistral", "api_key": "${TEST_MISTRAL_KEY}"}
        )
        assert cfg.api_key == "from-env"

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("TEST_MISTRAL_KEY", raising=False)
        with pytest.raises(ValueError, match="TEST_MISTRAL_KEY"):
            parse_llm_config({"provider": "mistral", "api_key": "${TEST_MISTRAL_KEY}"})

    def test_missing_api_key_raises(self):
        with pytest.raises(ValueError, match="api_key"):
            parse_llm_config({"provider": "mistral"})

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown llm.provider"):
            parse_llm_config({"provider": "gpt9000", "api_key": "x"})

    def test_not_a_mapping_raises(self):
        with pytest.raises(ValueError, match="mapping"):
            parse_llm_config(["mistral"])

    def test_model(self):
        cfg = parse_llm_config(
            {"provider": "mistral", "api_key": "x", "model": "mistral-large-latest"}
        )
        assert cfg.model == "mistral-large-latest"

    def test_anthropic_defaults(self):
        cfg = parse_llm_config({"provider": "anthropic", "api_key": "sk-ant"})
        assert cfg.enabled is True
        assert cfg.provider == "anthropic"
        assert cfg.model is None  # default applied by the adapter at call time

    def test_openai_defaults(self):
        cfg = parse_llm_config({"provider": "openai", "api_key": "sk-oai"})
        assert cfg.enabled is True
        assert cfg.provider == "openai"
        assert cfg.model is None

    def test_openrouter_requires_model(self):
        # OpenRouter routes to many models, so there's no default to fall back
        # on — `llm.model` must be set (fail-at-startup, like the rest).
        with pytest.raises(ValueError, match="no default model"):
            parse_llm_config({"provider": "openrouter", "api_key": "x"})

    def test_openrouter_with_model(self):
        cfg = parse_llm_config(
            {
                "provider": "openrouter",
                "api_key": "x",
                "model": "anthropic/claude-3.5-sonnet",
            }
        )
        assert cfg.enabled is True
        assert cfg.model == "anthropic/claude-3.5-sonnet"

    def test_all_providers_registered(self):
        assert known_providers() == [
            "anthropic",
            "mistral",
            "openai",
            "openrouter",
        ]

    def test_moved_component_knobs_raise(self):
        # max_rows / cache_ttl are <Ask /> attributes, not provider config —
        # the provider block stays reusable by future LLM-backed features.
        with pytest.raises(ValueError, match="max_rows moved"):
            parse_llm_config({"provider": "mistral", "api_key": "x", "max_rows": 10})
        with pytest.raises(ValueError, match="cache_ttl moved"):
            parse_llm_config({"provider": "mistral", "api_key": "x", "cache_ttl": 60})

    def test_create_adapter_requires_config(self):
        with pytest.raises(ValueError, match="no LLM provider"):
            create_adapter(LLMConfig())

    def test_resolve_model_name(self):
        # Falls back to the provider's DEFAULT_MODEL when llm.model is unset...
        assert (
            resolve_model_name(LLMConfig(provider="mistral", api_key="x"))
            == "mistral-small-latest"
        )
        assert (
            resolve_model_name(LLMConfig(provider="anthropic", api_key="x"))
            == "claude-haiku-4-5"
        )
        # ...and honors an explicit override.
        assert (
            resolve_model_name(
                LLMConfig(provider="mistral", api_key="x", model="mistral-large-latest")
            )
            == "mistral-large-latest"
        )
        # No provider configured → empty (the endpoint 503s before this matters).
        assert resolve_model_name(LLMConfig()) == ""


# --------------------------------------------------------------------------- #
# Provider adapters (request mapping, with the SDK client faked out)
# --------------------------------------------------------------------------- #
def _obj(**kwargs):
    """An anonymous attribute bag (SDK response shapes are attr-based)."""
    return type("O", (), kwargs)


class _FakeAnthropicClient:
    """Mimics anthropic.Anthropic — records create()/stream() kwargs."""

    class _Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _Stream:
        """messages.stream() context manager exposing .text_stream."""

        def __init__(self, chunks):
            self.text_stream = iter(chunks)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.create_kwargs = kwargs
            return type("R", (), {"content": [_FakeAnthropicClient._Block("**Hi**")]})

        def stream(self, **kwargs):
            self._outer.stream_kwargs = kwargs
            return _FakeAnthropicClient._Stream(["**H", "i**"])

    def __init__(self):
        self.create_kwargs = None
        self.stream_kwargs = None
        self.messages = _FakeAnthropicClient._Messages(self)


class _FakeOpenAIClient:
    """Mimics openai.OpenAI — records create() kwargs; streams on stream=True."""

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.create_kwargs = kwargs
            if kwargs.get("stream"):
                delta = lambda text: _obj(delta=_obj(content=text))  # noqa: E731
                return iter(
                    [
                        _obj(choices=[delta("**H")]),
                        _obj(choices=[delta("i**")]),
                        _obj(choices=[]),  # terminal/usage chunk — must be skipped
                    ]
                )
            msg = type("M", (), {"content": "**Hi**"})
            choice = type("C", (), {"message": msg})
            return type("R", (), {"choices": [choice]})

    def __init__(self):
        self.create_kwargs = None
        self.chat = type("Chat", (), {"completions": _FakeOpenAIClient._Completions(self)})


class _FakeMistralClient:
    """Mimics mistralai.Mistral's chat.stream() event shape."""

    class _Chat:
        def __init__(self, outer):
            self._outer = outer

        def stream(self, **kwargs):
            self._outer.stream_kwargs = kwargs
            event = lambda text: _obj(  # noqa: E731
                data=_obj(choices=[_obj(delta=_obj(content=text))])
            )
            return iter([event("**H"), event("i**"), _obj(data=_obj(choices=[]))])

    def __init__(self):
        self.stream_kwargs = None
        self.chat = _FakeMistralClient._Chat(self)


class TestAdapters:
    def test_anthropic_maps_request(self):
        adapter = AnthropicAdapter(LLMConfig(provider="anthropic", api_key="x"))
        adapter._client = _FakeAnthropicClient()  # bypass lazy SDK import
        out = adapter.complete("sys", "user prompt")
        assert out == "**Hi**"
        kw = adapter._client.create_kwargs
        assert kw["model"] == "claude-haiku-4-5"  # default
        assert kw["system"] == "sys"
        assert kw["messages"] == [{"role": "user", "content": "user prompt"}]
        assert kw["max_tokens"] == AnthropicAdapter.MAX_TOKENS

    def test_anthropic_model_override(self):
        adapter = AnthropicAdapter(
            LLMConfig(provider="anthropic", api_key="x", model="claude-opus-4-8")
        )
        adapter._client = _FakeAnthropicClient()
        adapter.complete("sys", "p")
        assert adapter._client.create_kwargs["model"] == "claude-opus-4-8"

    def test_openai_maps_request(self):
        adapter = OpenAIAdapter(LLMConfig(provider="openai", api_key="x"))
        adapter._client = _FakeOpenAIClient()
        out = adapter.complete("sys", "user prompt")
        assert out == "**Hi**"
        kw = adapter._client.create_kwargs
        assert kw["model"] == "gpt-4o-mini"  # default
        assert kw["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user prompt"},
        ]

    def test_openrouter_reuses_openai_path_with_explicit_model(self):
        adapter = OpenRouterAdapter(
            LLMConfig(
                provider="openrouter",
                api_key="x",
                model="anthropic/claude-3.5-sonnet",
            )
        )
        adapter._client = _FakeOpenAIClient()
        adapter.complete("sys", "p")
        assert adapter._client.create_kwargs["model"] == "anthropic/claude-3.5-sonnet"
        # OpenRouter is just the OpenAI client pointed at a different endpoint.
        assert OpenRouterAdapter.BASE_URL == "https://openrouter.ai/api/v1"
        assert isinstance(adapter, OpenAIAdapter)

    def test_create_adapter_returns_right_class(self):
        a = create_adapter(LLMConfig(provider="anthropic", api_key="x"))
        assert isinstance(a, AnthropicAdapter)
        o = create_adapter(LLMConfig(provider="openai", api_key="x"))
        assert isinstance(o, OpenAIAdapter) and not isinstance(o, OpenRouterAdapter)
        r = create_adapter(
            LLMConfig(provider="openrouter", api_key="x", model="x/y")
        )
        assert isinstance(r, OpenRouterAdapter)

    def test_base_stream_falls_back_to_complete(self):
        # An adapter without native streaming still works behind the SSE
        # endpoint: the base stream_complete degrades to one chunk.
        fake = FakeAdapter(reply="whole answer")
        assert list(fake.stream_complete("sys", "p")) == ["whole answer"]

    def test_anthropic_stream_maps_request(self):
        adapter = AnthropicAdapter(LLMConfig(provider="anthropic", api_key="x"))
        adapter._client = _FakeAnthropicClient()
        assert list(adapter.stream_complete("sys", "p")) == ["**H", "i**"]
        kw = adapter._client.stream_kwargs
        assert kw["model"] == "claude-haiku-4-5"
        assert kw["system"] == "sys"
        assert kw["max_tokens"] == AnthropicAdapter.MAX_TOKENS

    def test_openai_stream_maps_request(self):
        adapter = OpenAIAdapter(LLMConfig(provider="openai", api_key="x"))
        adapter._client = _FakeOpenAIClient()
        # Empty-choices (usage/terminal) chunks are skipped, not crashed on.
        assert list(adapter.stream_complete("sys", "p")) == ["**H", "i**"]
        kw = adapter._client.create_kwargs
        assert kw["stream"] is True
        assert kw["messages"][0] == {"role": "system", "content": "sys"}

    def test_mistral_stream_maps_request(self):
        adapter = MistralAdapter(LLMConfig(provider="mistral", api_key="x"))
        adapter._client = _FakeMistralClient()
        assert list(adapter.stream_complete("sys", "p")) == ["**H", "i**"]
        kw = adapter._client.stream_kwargs
        assert kw["model"] == "mistral-small-latest"


# --------------------------------------------------------------------------- #
# Prompt registry
# --------------------------------------------------------------------------- #
class TestAskRegistry:
    def test_id_is_deterministic(self):
        a = ask_id([("sales", "main")], "What changed?")
        b = ask_id([("sales", "main")], "What changed?")
        assert a == b

    def test_id_varies_with_inputs(self):
        base = ask_id([("sales", "main")], "What changed?")
        assert ask_id([("sales", "main")], "Other prompt") != base
        assert ask_id([("other", "main")], "What changed?") != base
        assert ask_id([("sales", "warehouse")], "What changed?") != base
        # max_rows changes the payload the model sees → must bust the cache id.
        assert ask_id([("sales", "main")], "What changed?", max_rows=5) != base

    def test_id_covers_every_query_pair(self):
        # A multi-query ask hashes all pairs: adding, changing, or reordering
        # a referenced query must produce a fresh id (⇒ a fresh answer).
        single = ask_id([("sales", "main")], "Why?")
        multi = ask_id([("sales", "main"), ("churn", "main")], "Why?")
        assert multi != single
        assert ask_id([("sales", "main"), ("churn", "other")], "Why?") != multi
        assert ask_id([("churn", "main"), ("sales", "main")], "Why?") != multi

    def test_register_and_lookup(self):
        d = register_ask_def([("sales", "main")], "Why?")
        assert get_ask_def(d.id) == d
        assert get_ask_def("nope") is None

    def test_register_defaults(self):
        d = register_ask_def([("sales", "main")], "Why?")
        assert d.max_rows == DEFAULT_MAX_ROWS
        assert d.cache_ttl == DEFAULT_ANSWER_TTL
        assert d.page_title == ""
        assert d.page_description == ""
        # The primary-pair convenience accessors read the first pair.
        assert d.queries == (("sales", "main"),)
        assert d.query_name == "sales"
        assert d.connector == "main"

    def test_page_context_joins_the_hash(self):
        # Page title/description feed the prompt, so changed context must
        # produce a different id (⇒ a fresh answer).
        base = ask_id([("sales", "main")], "Why?")
        assert ask_id([("sales", "main")], "Why?", page_title="Sales") != base
        assert ask_id([("sales", "main")], "Why?", page_description="Q3") != base

    def test_register_threads_page_context(self):
        d = register_ask_def(
            [("sales", "main")], "Why?", page_title="Sales", page_description="Q3 numbers"
        )
        assert d.page_title == "Sales"
        assert d.page_description == "Q3 numbers"
        assert d.id == ask_id(
            [("sales", "main")], "Why?", DEFAULT_MAX_ROWS, "Sales", "Q3 numbers"
        )


# --------------------------------------------------------------------------- #
# Component render (via render_page)
# --------------------------------------------------------------------------- #
ASK_PAGE = """# Sales

:::query name=by_region connector=main
SELECT region, SUM(amount) AS total FROM sales
WHERE (region = '${region}' OR '${region}' = '')
GROUP BY region ORDER BY total DESC
:::

<Ask data={by_region} ask="Which region leads and why?" max_rows=2 />
"""

# A multi-query ask (`data={a,b}`): one prompt pinned to two query results,
# each with its own `${param}` filter so the answer-cache union is observable.
MULTI_ASK_PAGE = """# Overview

:::query name=by_region connector=main
SELECT region, SUM(amount) AS total FROM sales
WHERE (region = '${region}' OR '${region}' = '')
GROUP BY region ORDER BY total DESC
:::

:::query name=churn_by_region connector=main
SELECT region, SUM(churned) AS churned FROM churn
WHERE (segment = '${segment}' OR '${segment}' = '')
GROUP BY region
:::

<Ask data={by_region,churn_by_region} ask="Is churn driving the dip?" />
"""

MULTI_ASK_ID = ask_id(
    [("by_region", "main"), ("churn_by_region", "main")],
    "Is churn driving the dip?",
)


class TestAskComponent:
    def test_registers_prompt_and_emits_placeholder(self):
        rendered = render_page(ASK_PAGE, connectors={})
        assert 'data-async-component="ask"' in rendered.body_html
        assert len(rendered.ask_defs) == 1
        ask = rendered.ask_defs[0]
        assert ask.queries == (("by_region", "main"),)
        assert ask.query_name == "by_region"
        assert ask.connector == "main"
        assert ask.prompt == "Which region leads and why?"
        assert get_ask_def(ask.id) == ask
        # The placeholder carries only the opaque id, never the prompt.
        assert ask.id in rendered.body_html
        assert "Which region leads" not in rendered.body_html

    def test_attr_knobs_thread_through(self):
        # max_rows / cache_ttl are component attributes (defaults on the
        # component, overridable per tag) — not `llm:` config.
        rendered = render_page(ASK_PAGE, connectors={})
        ask = rendered.ask_defs[0]
        assert ask.max_rows == 2  # from the tag
        assert ask.cache_ttl == DEFAULT_ANSWER_TTL  # component default

        source = ASK_PAGE.replace("max_rows=2", "max_rows=7 cache_ttl=120")
        ask2 = render_page(source, connectors={}).ask_defs[0]
        assert ask2.max_rows == 7
        assert ask2.cache_ttl == 120

    def test_replay_attr_threads_into_config(self):
        # The typewriter replay policy is a client concern: it rides the
        # data-config JSON, never the AskDef (it must not bust the answer cache).
        rendered = render_page(ASK_PAGE, connectors={})
        assert "&quot;replay&quot;: &quot;once&quot;" in rendered.body_html  # default

        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 replay="always"')
        rendered = render_page(source, connectors={})
        assert "&quot;replay&quot;: &quot;always&quot;" in rendered.body_html
        # Same knobs otherwise ⇒ same ask id: replay stays out of the hash.
        assert rendered.ask_defs[0].id == THE_ASK_ID

        # A bare boolean maps to the obvious mode; junk falls back to "once".
        source = ASK_PAGE.replace("max_rows=2", "max_rows=2 replay=false")
        assert "&quot;replay&quot;: &quot;off&quot;" in render_page(
            source, connectors={}
        ).body_html
        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 replay="sideways"')
        assert "&quot;replay&quot;: &quot;once&quot;" in render_page(
            source, connectors={}
        ).body_html

    def test_header_is_ai_badge_not_text_by_default(self):
        # Provenance stays visible as a quiet sparkle badge (tooltip on hover),
        # not an uppercase "COMMENTARY" banner; label= opts into heading text.
        rendered = render_page(ASK_PAGE, connectors={})
        assert "dashdown-ask-badge" in rendered.body_html
        assert 'title="AI-generated commentary"' in rendered.body_html
        # The sparkle carries a small "AI" wordmark — provenance is legible,
        # not just iconographic.
        assert (
            '<span class="dashdown-ask-badge-text" aria-hidden="true">AI</span>'
            in rendered.body_html
        )
        assert "Commentary" not in rendered.body_html
        assert "dashdown-ask-label" not in rendered.body_html
        # Exactly one model-attribution slot, inside the badge (hover-revealed);
        # ask.js binds it with a single querySelector.
        assert rendered.body_html.count("dashdown-ask-model") == 1
        assert (
            '<span class="dashdown-ask-model" hidden></span></span>'
            in rendered.body_html
        )

        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 label="Insights"')
        rendered = render_page(source, connectors={})
        assert "dashdown-ask-badge" in rendered.body_html  # badge stays
        assert ">Insights</span>" in rendered.body_html

    def test_lazy_by_default_optout_threads(self):
        # An unseen ask must not spend LLM credits: lazy rides the config so
        # ask.js defers loading until the card nears the viewport.
        rendered = render_page(ASK_PAGE, connectors={})
        assert "&quot;lazy&quot;: true" in rendered.body_html

        source = ASK_PAGE.replace("max_rows=2", "max_rows=2 lazy=false")
        rendered = render_page(source, connectors={})
        assert "&quot;lazy&quot;: false" in rendered.body_html

    def test_highlight_defaults_to_own_query(self):
        # Hover provenance: ask.js glows the data-query-name nodes named in
        # config.highlight_queries — by default the ask's own data query.
        rendered = render_page(ASK_PAGE, connectors={})
        assert (
            "&quot;highlight_queries&quot;: [&quot;by_region&quot;]"
            in rendered.body_html
        )

        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 highlight="daily, totals"')
        rendered = render_page(source, connectors={})
        assert (
            "&quot;highlight_queries&quot;: [&quot;daily&quot;, &quot;totals&quot;]"
            in rendered.body_html
        )

        source = ASK_PAGE.replace("max_rows=2", "max_rows=2 highlight=false")
        rendered = render_page(source, connectors={})
        assert "&quot;highlight_queries&quot;: []" in rendered.body_html

    def test_multi_query_ref_registers_all_pairs(self):
        rendered = render_page(MULTI_ASK_PAGE, connectors={})
        assert len(rendered.ask_defs) == 1
        ask = rendered.ask_defs[0]
        assert ask.queries == (("by_region", "main"), ("churn_by_region", "main"))
        assert ask.id == MULTI_ASK_ID
        # Highlight + the client config default to every referenced query.
        assert (
            "&quot;highlight_queries&quot;: [&quot;by_region&quot;, &quot;churn_by_region&quot;]"
            in rendered.body_html
        )
        assert (
            "&quot;query_names&quot;: [&quot;by_region&quot;, &quot;churn_by_region&quot;]"
            in rendered.body_html
        )
        assert 'data-query-name="by_region,churn_by_region"' in rendered.body_html

    def test_multi_query_resolves_connector_per_name(self):
        # Each name in the comma list binds its own connector — a cross-source
        # ask ((a, main), (b, warehouse)) is a first-class pair list.
        source = (
            "# T\n\n"
            ":::query name=a connector=main\nSELECT 1\n:::\n\n"
            ":::query name=b connector=warehouse\nSELECT 2\n:::\n\n"
            '<Ask data={a,b} ask="Compare." />\n'
        )
        rendered = render_page(source, connectors={})
        assert rendered.ask_defs[0].queries == (("a", "main"), ("b", "warehouse"))

    def test_inline_variant_drops_card_chrome(self):
        rendered = render_page(ASK_PAGE, connectors={})
        assert "card bg-base-100" in rendered.body_html  # card is the default

        source = ASK_PAGE.replace("max_rows=2", "max_rows=2 inline")
        rendered = render_page(source, connectors={})
        assert "dashdown-ask-inline" in rendered.body_html
        assert "card bg-base-100" not in rendered.body_html
        # Provenance/refresh markup stays — visibility is a CSS hover concern.
        assert "dashdown-ask-badge" in rendered.body_html
        assert "dashdown-ask-refresh" in rendered.body_html
        # Same id — presentation attrs never bust the answer cache.
        assert rendered.ask_defs[0].id == THE_ASK_ID

    def test_font_size_preset_and_explicit_length(self):
        # Presets follow the Tailwind text scale; the value lands as an inline
        # style on the body div (a generated text-* class would silently miss
        # the prebuilt vendor CSS). Default: no style attr — stylesheet rules.
        rendered = render_page(ASK_PAGE, connectors={})
        assert "font-size" not in rendered.body_html

        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 font_size="xs"')
        rendered = render_page(source, connectors={})
        assert (
            '<div class="dashdown-ask-body" style="font-size:0.75rem">'
            in rendered.body_html
        )
        # Presentation only — same id, so restyling never busts the answer cache.
        assert rendered.ask_defs[0].id == THE_ASK_ID

        source = ASK_PAGE.replace("max_rows=2", 'max_rows=2 font_size="0.8rem"')
        rendered = render_page(source, connectors={})
        assert 'style="font-size:0.8rem"' in rendered.body_html

    def test_font_size_junk_is_dropped(self):
        # The value lands inside a style attribute: only a preset or a bare
        # number+unit passes. Junk falls back to the stylesheet default (same
        # forgiveness as replay's junk handling) — never an errored card.
        for junk in ("1em;position:fixed", "url(x)", "huge", "12", "calc(1rem)"):
            source = ASK_PAGE.replace(
                "max_rows=2", f'max_rows=2 font_size="{junk}"'
            )
            rendered = render_page(source, connectors={})
            assert "font-size" not in rendered.body_html, junk
            assert len(rendered.ask_defs) == 1  # the card still renders

    def test_refresh_optout_threads_and_removes_button(self):
        rendered = render_page(ASK_PAGE, connectors={})
        assert rendered.ask_defs[0].allow_refresh is True
        assert "dashdown-ask-refresh" in rendered.body_html

        source = ASK_PAGE.replace("max_rows=2", "max_rows=2 refresh=false")
        rendered = render_page(source, connectors={})
        assert rendered.ask_defs[0].allow_refresh is False
        assert "dashdown-ask-refresh" not in rendered.body_html
        # Same id — refresh isn't part of the hash, so toggling it can't bust
        # the answer cache.
        assert rendered.ask_defs[0].id == THE_ASK_ID

    def test_inner_content_as_prompt(self):
        source = ASK_PAGE.replace(
            '<Ask data={by_region} ask="Which region leads and why?" max_rows=2 />',
            "<Ask data={by_region}>Which region **leads**?</Ask>",
        )
        rendered = render_page(source, connectors={})
        assert len(rendered.ask_defs) == 1
        # Markdown/HTML in the inner content is stripped down to text.
        assert rendered.ask_defs[0].prompt == "Which region leads ?"

    def test_missing_data_is_inline_error(self):
        rendered = render_page('# T\n\n<Ask ask="Why?" />\n', connectors={})
        assert "Ask requires data=" in rendered.body_html
        assert rendered.ask_defs == []

    def test_missing_prompt_is_inline_error(self):
        rendered = render_page(
            "# T\n\n:::query name=q connector=main\nSELECT 1\n:::\n\n<Ask data={q} />\n",
            connectors={},
        )
        assert "Ask requires an" in rendered.body_html
        assert rendered.ask_defs == []

    def test_not_stripped_in_static_build(self):
        rendered = render_page(ASK_PAGE, connectors={}, static_build=True)
        assert 'data-async-component="ask"' in rendered.body_html
        assert len(rendered.ask_defs) == 1

    def test_page_frontmatter_threads_into_ask_def(self):
        source = (
            "---\ntitle: Sales Overview\ndescription: Regional numbers\n---\n\n"
            + ASK_PAGE
        )
        rendered = render_page(source, connectors={})
        ask = rendered.ask_defs[0]
        assert ask.page_title == "Sales Overview"
        assert ask.page_description == "Regional numbers"
        # Different page context ⇒ a different id than the bare page's block.
        assert ask.id != render_page(ASK_PAGE, connectors={}).ask_defs[0].id

    def test_semantic_metric_ref_resolves_to_synthetic_query(self):
        """`<Ask metric={model.metric} by={model.dim} />` binds to the same
        synthetic query a chart with those attrs would, recording the ref so the
        pipeline compiles + registers it. No BSL needed — the ref resolution and
        ask-def wiring are pure; only the later spec *compile* needs ibis."""
        from dashdown.components.base import RenderContext
        from dashdown.render.components import render_components
        from dashdown.semantic import SemanticModelHandle

        dims = {"region", "status"}
        measures = {"revenue", "orders"}
        handle = SemanticModelHandle(
            name="sales",
            connector="warehouse",  # not "main" — proves the connector tracks the model
            file_config={},
            table_connectors={"orders": "warehouse"},
            profile=None,
            profile_path=None,
            measures=measures,
            dimensions=dims,
            time_dimension=None,
            measure_formats={},
            dim_lookup={d: d for d in dims},
            measure_lookup={m: m for m in measures},
        )
        ctx = RenderContext(queries={}, semantic_models={"sales": handle})
        html = render_components(
            '<Ask metric={sales.revenue} by={sales.region} ask="Which region leads?" />',
            ctx,
        )
        assert 'data-async-component="ask"' in html
        assert len(ctx.ask_defs) == 1
        ask = ctx.ask_defs[0]
        # Same synthetic name a chart builds, so they share the registered spec.
        assert ask.query_name == "_sem.sales.revenue.by.region"
        assert ask.connector == "warehouse"
        # Recorded for the pipeline to compile into a PythonQuerySpec.
        assert "_sem.sales.revenue.by.region" in ctx.semantic_refs


# --------------------------------------------------------------------------- #
# Chart `explain` affordance (sugar over the ask machinery)
# --------------------------------------------------------------------------- #
EXPLAIN_PAGE = """# Sales

:::query name=by_region connector=main
SELECT region, SUM(amount) AS total FROM sales GROUP BY region
:::

<BarChart data={by_region} x="region" y="total" title="Revenue by region" explain />
"""


class TestChartExplain:
    def test_explain_registers_canned_ask_and_emits_footer(self):
        rendered = render_page(EXPLAIN_PAGE, connectors={})
        # An ordinary AskDef with a canned prompt naming the chart — the
        # opaque-id model is untouched (viewers still can't send prompts).
        assert len(rendered.ask_defs) == 1
        ask = rendered.ask_defs[0]
        assert ask.queries == (("by_region", "main"),)
        assert 'bar chart titled "Revenue by region"' in ask.prompt
        assert "Explain what is notable" in ask.prompt
        assert get_ask_def(ask.id) == ask
        # Button + hidden footer; the placeholder carries only the opaque id.
        assert "dashdown-explain-btn" in rendered.body_html
        assert "dashdown-explain-panel" in rendered.body_html
        assert ask.id in rendered.body_html
        assert "Explain what is notable" not in rendered.body_html
        # The footer is initialized on first click by initExplain — it must
        # NOT be an async ask component (initAllAsks would eager-load it).
        assert 'data-async-component="ask"' not in rendered.body_html
        # The footer never glows its own host chart on hover.
        assert "&quot;highlight_queries&quot;: []" in rendered.body_html
        # The fixed height moves onto the inner region so the card can grow
        # when the footer opens.
        assert "dashdown-chart-region" in rendered.body_html
        assert 'style="height:300px"' in rendered.body_html

    def test_explain_id_is_deterministic_across_renders(self):
        first = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        second = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        assert first.id == second.id  # answer cache absorbs repeat page loads

    def test_cache_ttl_attr_threads_through(self):
        # Same spelling and default as <Ask cache_ttl=…>; like there, it only
        # affects expiry — tuning it must not mint a new id (fresh answer).
        default = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        assert default.cache_ttl == DEFAULT_ANSWER_TTL

        source = EXPLAIN_PAGE.replace(" explain", " explain cache_ttl=86400")
        tuned = render_page(source, connectors={}).ask_defs[0]
        assert tuned.cache_ttl == 86400
        assert tuned.id == default.id  # cache_ttl stays out of the id hash

    def test_explain_custom_prompt(self):
        source = EXPLAIN_PAGE.replace(
            "explain", 'explain="Why does the North lead?"'
        )
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.prompt == "Why does the North lead?"

    def test_untitled_chart_gets_generic_prompt(self):
        source = EXPLAIN_PAGE.replace(' title="Revenue by region"', "")
        ask = render_page(source, connectors={}).ask_defs[0]
        assert "shown as a bar chart:" in ask.prompt
        assert "titled" not in ask.prompt

    def test_no_explain_keeps_classic_markup(self):
        source = EXPLAIN_PAGE.replace(" explain", "")
        rendered = render_page(source, connectors={})
        assert rendered.ask_defs == []
        assert "dashdown-explain" not in rendered.body_html
        # Height stays inline on the card root, exactly as before.
        assert "dashdown-chart-region" not in rendered.body_html
        assert "height:300px" in rendered.body_html

    def test_explain_false_is_off(self):
        source = EXPLAIN_PAGE.replace(" explain", " explain=false")
        rendered = render_page(source, connectors={})
        assert rendered.ask_defs == []
        assert "dashdown-explain" not in rendered.body_html

    def test_explain_ships_in_static_build(self):
        # Explain works in exports: the button + footer render, and the AskDef
        # joins ask_defs so `_export_ask` bakes the answer (+ annotations) that
        # ask.js's static branch fetches on first open — click → retrieve →
        # show, exactly like serve mode.
        rendered = render_page(EXPLAIN_PAGE, connectors={}, static_build=True)
        assert len(rendered.ask_defs) == 1
        assert "dashdown-explain-btn" in rendered.body_html
        assert "dashdown-explain-panel" in rendered.body_html
        # Same deterministic id as a live render, so the baked snapshot and a
        # live answer cache never diverge for the same chart.
        live = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        assert rendered.ask_defs[0].id == live.id

    def test_combo_chart_rides_the_shared_shell(self):
        source = """# T

:::query name=q connector=main
SELECT month, revenue, orders FROM t
:::

<ComboChart data={q} x="month" bars="revenue" lines="orders" explain />
"""
        rendered = render_page(source, connectors={})
        assert "dashdown-explain-btn" in rendered.body_html
        assert len(rendered.ask_defs) == 1
        assert rendered.ask_defs[0].queries == (("q", "main"),)
        assert "combo chart" in rendered.ask_defs[0].prompt

    def test_semantic_chart_explain_binds_synthetic_query(self):
        from dashdown.components.base import RenderContext
        from dashdown.render.components import render_components
        from dashdown.semantic import SemanticModelHandle

        dims = {"region"}
        measures = {"revenue"}
        handle = SemanticModelHandle(
            name="sales",
            connector="warehouse",
            file_config={},
            table_connectors={"orders": "warehouse"},
            profile=None,
            profile_path=None,
            measures=measures,
            dimensions=dims,
            time_dimension=None,
            measure_formats={},
            dim_lookup={d: d for d in dims},
            measure_lookup={m: m for m in measures},
        )
        ctx = RenderContext(queries={}, semantic_models={"sales": handle})
        html = render_components(
            "<BarChart metric={sales.revenue} by={sales.region} explain />", ctx
        )
        assert "dashdown-explain-btn" in html
        assert len(ctx.ask_defs) == 1
        ask = ctx.ask_defs[0]
        # Same synthetic query name/connector a chart registers, so the ask
        # endpoint resolves the exact spec this chart reads.
        assert ask.queries == (("_sem.sales.revenue.by.region", "warehouse"),)


# --------------------------------------------------------------------------- #
# Payload cap + answer rendering
# --------------------------------------------------------------------------- #
class TestPayload:
    def test_caps_rows_and_notes_truncation(self):
        result = QueryResult(
            columns=["region", "total"],
            rows=[[f"r{i}", i] for i in range(10)],
        )
        text = format_result_for_llm(result, max_rows=3)
        assert "Rows (first 3 of 10; truncated):" in text
        assert "r2" in text
        assert "r3" not in text

    def test_includes_columns_and_types(self):
        result = QueryResult(columns=["region", "total"], rows=[["North", 12.5]])
        text = format_result_for_llm(result, max_rows=50)
        assert "region (str)" in text
        assert "total (float)" in text
        assert "Rows (1):" in text

    def test_null_cells(self):
        result = QueryResult(columns=["a"], rows=[[None]])
        assert "NULL" in format_result_for_llm(result, max_rows=5)

    def test_generate_answer_renders_markdown(self):
        fake = FakeAdapter(reply="Top is **North**.")
        ask = AskDef(id="x", queries=(("q", "main"),), prompt="Why?", max_rows=5)
        result = QueryResult(columns=["a"], rows=[[1]])
        html, text, annotations = generate_answer(ask, [result], fake)
        assert "<strong>North</strong>" in html
        # The raw answer rides along for the client-side typewriter replay.
        assert text == "Top is **North**."
        # A plain (no chart_context) ask never carries annotations.
        assert annotations == []
        # Prompt + data both reached the model.
        system, prompt = fake.calls[0]
        assert "Why?" in prompt
        assert "a (int)" in prompt
        # …and the annotation protocol never leaks into a plain ask's prompt.
        assert "annotations" not in prompt

    def test_llm_html_output_is_escaped(self):
        # A prompt-injected model can't smuggle markup into the page.
        html = render_markdown_text("<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_prompt_includes_grounding_context(self):
        fake = FakeAdapter()
        ask = AskDef(
            id="x",
            queries=(("q", "main"),),
            prompt="Why?",
            page_title="Sales Overview",
            page_description="Regional numbers",
        )
        result = QueryResult(columns=["a"], rows=[[1]])
        generate_answer(ask, [result], fake, {"region": "East", "blank": ""})
        _, prompt = fake.calls[0]
        assert "Dashboard page: Sales Overview — Regional numbers" in prompt
        assert f"Today's date: {date.today().isoformat()}" in prompt
        assert "Active filters" in prompt
        assert "- region = East" in prompt
        assert "blank" not in prompt  # empty values aren't active filters

    def test_prompt_omits_absent_context(self):
        ask = AskDef(id="x", queries=(("q", "main"),), prompt="Why?")
        prompt = build_ask_prompt(ask, [QueryResult(columns=["a"], rows=[[1]])])
        assert "Dashboard page:" not in prompt
        assert "Active filters" not in prompt
        assert "Today's date:" in prompt  # always present

    def test_prompt_labels_each_query_block(self):
        # A multi-query ask concatenates one labeled data block per query, in
        # `queries` order, so the model can reason across datasets by name.
        ask = AskDef(id="x", queries=(("revenue", "main"), ("churn", "main")), prompt="Why?")
        prompt = build_ask_prompt(
            ask,
            [
                QueryResult(columns=["month", "total"], rows=[["Jan", 100]]),
                QueryResult(columns=["month", "churned"], rows=[["Jan", 7]]),
            ],
        )
        assert "Result of query 'revenue':" in prompt
        assert "Result of query 'churn':" in prompt
        assert prompt.index("'revenue'") < prompt.index("'churn'")
        assert "total" in prompt
        assert "churned" in prompt

    def test_stream_answer_uses_same_prompt(self):
        fake = StreamingFakeAdapter()
        ask = AskDef(id="x", queries=(("q", "main"),), prompt="Why?")
        result = QueryResult(columns=["a"], rows=[[1]])
        chunks = list(stream_answer(ask, [result], fake, {"region": "East"}))
        assert "".join(chunks) == "**North** leads the pack."
        _, prompt = fake.calls[0]
        assert "- region = East" in prompt


# --------------------------------------------------------------------------- #
# Answer cache helpers
# --------------------------------------------------------------------------- #
class TestAnswerCache:
    def test_relevant_params_filters_to_sql(self):
        sql = "SELECT * FROM t WHERE r = '${region}'"
        assert relevant_params(sql, {"region": "N", "other": "x"}) == {"region": "N"}

    def test_roundtrip_and_expiry(self):
        # The raw answer text is cached alongside the html for typewriter replay.
        cache_answer("id1", {"a": "1"}, "<p>hi</p>", ttl=60, text="hi")
        assert get_cached_answer("id1", {"a": "1"}) == ("<p>hi</p>", "hi", [])
        assert get_cached_answer("id1", {"a": "2"}) is None
        cache_answer("id2", {}, "<p>old</p>", ttl=-1)  # already expired
        assert get_cached_answer("id2", {}) is None

    def test_roundtrip_carries_annotations(self):
        # Chart annotations ride the same entry, so a cache hit ships the same
        # marks the fresh answer did.
        marks = [{"id": "a1", "type": "extremum", "kind": "max", "label": "Peak"}]
        cache_answer("id3", {}, "<p>hi</p>", ttl=60, text="hi", annotations=marks)
        assert get_cached_answer("id3", {}) == ("<p>hi</p>", "hi", marks)


# --------------------------------------------------------------------------- #
# Endpoint integration
# --------------------------------------------------------------------------- #
def _make_project(root, llm_block=True):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    yaml = "title: Ask Test\n"
    if llm_block is True:
        yaml += "llm:\n  provider: mistral\n  api_key: dummy\n"
    elif llm_block:  # a raw `llm:` yaml snippet (misconfiguration tests)
        yaml += llm_block
    (root / "dashdown.yaml").write_text(yaml, encoding="utf-8")
    (root / "sources.yaml").write_text(
        "main:\n  type: csv\n  directory: data\n", encoding="utf-8"
    )
    (root / "data" / "sales.csv").write_text(
        "region,amount\nNorth,100\nSouth,200\nWest,50\n", encoding="utf-8"
    )
    (root / "pages" / "index.md").write_text(ASK_PAGE, encoding="utf-8")


def _client_with_fake(tmp_path, llm_block=True, fake=None):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj, llm_block=llm_block)
    app = create_app(proj)
    app.state.project.llm_adapter = fake
    client = TestClient(app)
    # Render the page so the query + ask defs register in the global caches.
    assert client.get("/").status_code == 200
    return client, fake


THE_ASK_ID = ask_id(
    [("by_region", "main")],
    "Which region leads and why?",
    max_rows=2,  # set on the tag in ASK_PAGE; part of the id hash
)


def _add_multi_page(root):
    """Add the second dataset + the multi-query ask page to a project."""
    (root / "data" / "churn.csv").write_text(
        "region,segment,churned\nNorth,SMB,3\nSouth,ENT,5\n", encoding="utf-8"
    )
    (root / "pages" / "multi.md").write_text(MULTI_ASK_PAGE, encoding="utf-8")


def _multi_client(tmp_path, fake=None, extra_yaml="", auth=None):
    """Like _client_with_fake, but renders the multi-query ask page."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    _add_multi_page(proj)
    if extra_yaml:
        cfg = (proj / "dashdown.yaml").read_text(encoding="utf-8")
        (proj / "dashdown.yaml").write_text(cfg + extra_yaml, encoding="utf-8")
    app = create_app(proj)
    app.state.project.llm_adapter = fake
    client = TestClient(app)
    # Render the page so the query + ask defs register in the global caches.
    assert client.get("/multi", auth=auth).status_code == 200
    return client, fake


class TestAskEndpoint:
    def test_generates_and_caches(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())

        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 200
        body = r.json()
        assert body["cached"] is False
        assert "<strong>North</strong>" in body["html"]
        # The configured model (mistral default) is attributed to the reader.
        assert body["model"] == "mistral-small-latest"
        assert len(fake.calls) == 1

        # The raw answer rides along for the client-side typewriter replay.
        assert body["text"] == "**North** leads the pack."

        # Second identical request answers from the cache — no new LLM call, but
        # the model is still reported (resolved from config, not the cache).
        r2 = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r2.json()["cached"] is True
        assert r2.json()["model"] == "mistral-small-latest"
        # A cache hit still carries the raw text, so it can replay too.
        assert r2.json()["text"] == "**North** leads the pack."
        assert len(fake.calls) == 1

    def test_relevant_filter_change_regenerates(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?region=North")
        assert len(fake.calls) == 2

    def test_irrelevant_filter_hits_cache(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?unrelated=1")
        assert r.json()["cached"] is True
        assert len(fake.calls) == 1

    def test_refresh_bypasses_cache(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_refresh=1")
        assert r.json()["cached"] is False
        assert len(fake.calls) == 2

    def test_refresh_optout_enforced_server_side(self, tmp_path):
        # <Ask refresh=false>: hiding the ↻ button isn't enough — a crafted
        # `_refresh=1` request must not force a fresh (billable) LLM call.
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())
        # Re-register the page's ask with the opt-out (same id: allow_refresh
        # stays out of the hash, so this overwrites the def in place).
        register_ask_def(
            [("by_region", "main")],
            "Which region leads and why?",
            max_rows=2,
            allow_refresh=False,
        )
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_refresh=1")
        assert r.json()["cached"] is True
        assert len(fake.calls) == 1

    def test_row_cap_applies(self, tmp_path):
        # The <Ask /> tag sets max_rows=2; the CSV has 3 rows ordered by total
        # DESC (South, North, West) — West must not reach the model.
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        _, prompt = fake.calls[0]
        assert "truncated" in prompt
        assert "South" in prompt
        assert "West" not in prompt

    def test_unknown_id_404s(self, tmp_path):
        client, _ = _client_with_fake(tmp_path, fake=FakeAdapter())
        assert client.get("/_dashdown/api/ask/deadbeefdeadbeef").status_code == 404

    def test_unconfigured_llm_returns_notice(self, tmp_path):
        # No `llm:` block: the page and its data still work, so the ask card
        # gets a 200 "commentary not available" notice, not an error.
        client, _ = _client_with_fake(tmp_path, llm_block=False)
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 200
        body = r.json()
        assert body["html"] == ""
        assert "AI commentary is not available" in body["notice"]
        assert "no LLM provider is configured" in body["notice"]

    def test_provider_error_502s(self, tmp_path):
        class BoomAdapter(FakeAdapter):
            def complete(self, system, prompt):
                raise RuntimeError("rate limited")

        client, _ = _client_with_fake(tmp_path, fake=BoomAdapter())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 502
        assert "rate limited" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Graceful degradation — a broken `llm:` block disables AI commentary with a
# notice instead of refusing to serve/build (unlike auth, which stays strict).
# --------------------------------------------------------------------------- #
_MISCONFIGURED_LLM = (
    "llm:\n  provider: mistral\n  api_key: ${DASHDOWN_TEST_UNSET_VAR}\n"
)


class TestGracefulDegradation:
    def test_misconfigured_llm_does_not_fail_load(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DASHDOWN_TEST_UNSET_VAR", raising=False)
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj, llm_block=_MISCONFIGURED_LLM)
        project = load_project(proj)  # must not raise
        try:
            assert project.config.llm.enabled is False
            assert "DASHDOWN_TEST_UNSET_VAR" in project.config.llm.error
        finally:
            project.close()

    def test_misconfigured_llm_notice_stays_generic(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DASHDOWN_TEST_UNSET_VAR", raising=False)
        client, _ = _client_with_fake(tmp_path, llm_block=_MISCONFIGURED_LLM)
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 200
        notice = r.json()["notice"]
        assert "AI commentary is not available" in notice
        assert "misconfigured" in notice
        # Any viewer sees this text (and static exports bake it) — the config
        # detail (env var names, …) belongs in the server log, never here.
        assert "DASHDOWN_TEST_UNSET_VAR" not in notice
        assert "server log" in notice

    def test_unavailable_notice_wording(self):
        assert "no LLM provider is configured" in unavailable_notice(LLMConfig())
        broken = LLMConfig(error="env var SECRET_KEY not set")
        notice = unavailable_notice(broken)
        assert "misconfigured" in notice
        assert "SECRET_KEY" not in notice  # detail stays in the log

    def test_build_with_misconfigured_llm_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DASHDOWN_TEST_UNSET_VAR", raising=False)
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj, llm_block=_MISCONFIGURED_LLM)
        project = load_project(proj)
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()
        # Keyless build is expected to succeed: a notice payload, no failure.
        assert result.failed_asks == []
        snapshot = tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{THE_ASK_ID}.json"
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        # The baked notice is public — generic wording, no config internals.
        assert "misconfigured" in payload["notice"]
        assert "DASHDOWN_TEST_UNSET_VAR" not in payload["notice"]


# --------------------------------------------------------------------------- #
# Streaming endpoint (SSE) — `_stream=1` opts into event-stream on cache miss
# --------------------------------------------------------------------------- #
def _parse_sse(text):
    """Parse an SSE body into [(event, decoded-json-data), …]."""
    events = []
    for raw in text.split("\n\n"):
        if not raw.strip():
            continue
        event, data_lines = "message", []
        for line in raw.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        events.append((event, json.loads("\n".join(data_lines))))
    return events


class TestAskStreamingEndpoint:
    def test_streams_chunks_then_done(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=StreamingFakeAdapter())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.text)
        chunks = [d["text"] for e, d in events if e == "chunk"]
        assert chunks == ["**North** ", "leads ", "the pack."]
        (done,) = [d for e, d in events if e == "done"]
        # The final event carries the server-rendered sanitized HTML — the
        # exact payload the blocking JSON path produces.
        assert "<strong>North</strong>" in done["html"]
        assert done["model"] == "mistral-small-latest"
        assert done["cached"] is False

    def test_streamed_answer_lands_in_the_cache(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=StreamingFakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        # Cache hit stays a single JSON payload even with _stream=1 —
        # streaming only ever happens on the slow path.
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["cached"] is True
        assert "<strong>North</strong>" in r.json()["html"]
        # The streamed answer's raw text was cached alongside the html, so the
        # cache hit can replay the exact text the stream delivered.
        assert r.json()["text"] == "**North** leads the pack."
        assert len(fake.calls) == 1

    def test_adapter_without_native_streaming_sends_one_chunk(self, tmp_path):
        client, _ = _client_with_fake(tmp_path, fake=FakeAdapter())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        events = _parse_sse(r.text)
        chunks = [d["text"] for e, d in events if e == "chunk"]
        assert chunks == ["**North** leads the pack."]
        assert events[-1][0] == "done"

    def test_stream_error_reported_in_band(self, tmp_path):
        class BoomStream(FakeAdapter):
            def stream_complete(self, system, prompt):
                yield "part"
                raise RuntimeError("rate limited")

        client, _ = _client_with_fake(tmp_path, fake=BoomStream())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        assert r.status_code == 200  # headers were already sent — error rides in-band
        events = _parse_sse(r.text)
        assert events[-1][0] == "error"
        assert "rate limited" in events[-1][1]["error"]
        # A failed stream must not poison the answer cache.
        assert get_cached_answer(THE_ASK_ID, {}) is None

    def test_stream_prompt_carries_filter_context(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=StreamingFakeAdapter())
        client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1&region=North")
        _, prompt = fake.calls[0]
        assert "- region = North" in prompt


# --------------------------------------------------------------------------- #
# Multi-query asks (`data={a,b}`) — one answer grounded in several results
# --------------------------------------------------------------------------- #
class TestMultiQueryAskEndpoint:
    def test_generates_from_all_queries(self, tmp_path):
        client, fake = _multi_client(tmp_path, fake=FakeAdapter())
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}")
        assert r.status_code == 200, r.text
        assert r.json()["cached"] is False
        # One labeled data block per referenced query reached the model.
        _, prompt = fake.calls[0]
        assert "Result of query 'by_region':" in prompt
        assert "Result of query 'churn_by_region':" in prompt
        assert "total" in prompt  # sales columns
        assert "churned" in prompt  # churn columns

    def test_cache_key_unions_each_querys_relevant_params(self, tmp_path):
        client, fake = _multi_client(tmp_path, fake=FakeAdapter())
        client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}")
        # A filter only the SECOND query substitutes busts the answer cache…
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?segment=SMB")
        assert r.json()["cached"] is False
        # …as does one only the FIRST uses…
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?region=North")
        assert r.json()["cached"] is False
        assert len(fake.calls) == 3
        # …while a filter neither query substitutes stays a cache hit.
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?unrelated=1")
        assert r.json()["cached"] is True
        assert len(fake.calls) == 3

    def test_streaming_covers_all_queries(self, tmp_path):
        client, fake = _multi_client(tmp_path, fake=StreamingFakeAdapter())
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?_stream=1")
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.text)
        assert events[-1][0] == "done"
        _, prompt = fake.calls[0]
        assert "'by_region'" in prompt
        assert "'churn_by_region'" in prompt

    def test_unknown_second_query_404s(self, tmp_path):
        client, _ = _multi_client(tmp_path, fake=FakeAdapter())
        bad = register_ask_def([("by_region", "main"), ("ghost", "main")], "Why?")
        r = client.get(f"/_dashdown/api/ask/{bad.id}")
        assert r.status_code == 404
        assert "ghost" in r.json()["detail"]


class TestMultiQueryAskEmbedScope:
    _YAML = (
        "auth:\n  type: basic\n  username: admin\n  password: s3cret\n"
        "embed:\n  enabled: true\n  secret: topsecret\n"
    )

    def test_token_must_cover_every_query(self, tmp_path):
        # A multi-query ask reads several results, so an embed token scoped to
        # only one of them must NOT unlock the ask endpoint — otherwise a token
        # for a cheap query would exfiltrate commentary on a sensitive one.
        from dashdown.embed import sign_embed_token

        client, _ = _multi_client(
            tmp_path,
            fake=FakeAdapter(),
            extra_yaml=self._YAML,
            auth=("admin", "s3cret"),
        )
        partial = sign_embed_token("topsecret", "/multi", ["main:by_region"])
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?_embed={partial}")
        assert r.status_code == 401

        full = sign_embed_token(
            "topsecret", "/multi", ["main:by_region", "main:churn_by_region"]
        )
        r = client.get(f"/_dashdown/api/ask/{MULTI_ASK_ID}?_embed={full}")
        assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# <Ask> on semantic-layer data (Stage 18b) — end to end through the endpoint
# and the static build. Gated on the optional `semantic` extra (BSL/Ibis), like
# the semantic tests; the synthetic query is compiled + executed for real.
# --------------------------------------------------------------------------- #
import shutil
from pathlib import Path

_SEMANTIC_EXAMPLE = Path(__file__).parent / "fixtures" / "semantic_first_class"

_bsl_installed = True
try:  # the semantic extra
    import boring_semantic_layer  # noqa: F401
    import ibis  # noqa: F401
except ImportError:  # pragma: no cover
    _bsl_installed = False

needs_bsl = pytest.mark.skipif(not _bsl_installed, reason="requires dashdown-md[semantic]")


_SEM_ASK_PROMPT = "Which region leads?"


def _the_semantic_ask():
    """The AskDef our test page registered, selected by its prompt.

    The example's index.md now ships its *own* <Ask> demo, so a full build renders
    two semantic asks (same query, different prompt) — pick ours. Its synthetic
    ``query_name`` is BSL-canonical (the joined ``sales`` model prefixes names →
    ``_sem.sales.sales.revenue.by.sales.region``), so read the registered def
    rather than reconstructing the id by hand."""
    asks = [a for a in _ask_def_cache.values() if a.prompt == _SEM_ASK_PROMPT]
    assert len(asks) == 1, list(_ask_def_cache.values())
    ask = asks[0]
    assert ask.query_name.startswith("_sem.")
    return ask


def _semantic_project(tmp_path, *, llm_block=True):
    """A runnable copy of the vendored ``tests/fixtures/semantic_first_class``
    project + an <Ask metric=.../> page.

    Mirrors test_semantic's example_project (``sources.yaml`` is gitignored, so
    write a credential-free CSV one here). The committed fixture keeps its `llm:`
    block commented out, so appending one here is no duplicate."""
    dst = tmp_path / "sem_proj"
    shutil.copytree(
        _SEMANTIC_EXAMPLE,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.duckdb*", "sources.yaml"),
    )
    (dst / "sources.yaml").write_text("main:\n  type: csv\n  directory: data\n")
    cfg = (dst / "dashdown.yaml").read_text()
    if llm_block:
        cfg += "\nllm:\n  provider: mistral\n  api_key: dummy\n"
    (dst / "dashdown.yaml").write_text(cfg)
    (dst / "pages" / "ask_semantic.md").write_text(
        "# Ask Semantic\n\n"
        f'<Ask metric={{sales.revenue}} by={{sales.region}} ask="{_SEM_ASK_PROMPT}" />\n',
        encoding="utf-8",
    )
    return dst


@needs_bsl
class TestAskOnSemanticLayer:
    def test_endpoint_answers_semantic_ask(self, tmp_path):
        app = create_app(_semantic_project(tmp_path))
        fake = FakeAdapter()
        app.state.project.llm_adapter = fake
        client = TestClient(app)

        # Render the page so the synthetic semantic spec + ask def register.
        assert client.get("/ask_semantic").status_code == 200
        ask = _the_semantic_ask()
        assert ask.connector == "main"

        r = client.get(f"/_dashdown/api/ask/{ask.id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cached"] is False
        assert "<strong>North</strong>" in body["html"]  # FakeAdapter default reply
        # The compiled metric data actually reached the model (not an empty/404).
        _, prompt = fake.calls[0]
        assert "revenue" in prompt
        assert "region" in prompt

        # Second identical request is served from the answer cache.
        assert client.get(f"/_dashdown/api/ask/{ask.id}").json()["cached"] is True
        assert len(fake.calls) == 1

    def test_static_build_bakes_semantic_ask(self, tmp_path):
        from dashdown.project import load_project

        proj_dir = _semantic_project(tmp_path)
        project = load_project(proj_dir)
        project.llm_adapter = FakeAdapter()
        out = tmp_path / "dist"
        result = _build(project, out)

        ask = _the_semantic_ask()
        assert ask.id in result.asks  # baked, not in failed_asks
        snapshot = out / "_dashdown" / "data" / "_ask" / f"{ask.id}.json"
        assert snapshot.exists()
        baked = json.loads(snapshot.read_text())
        assert "error" not in baked
        assert "<strong>North</strong>" in baked["html"]


# --------------------------------------------------------------------------- #
# Static build baking
# --------------------------------------------------------------------------- #
class TestStaticBuild:
    def test_bakes_commentary_snapshot(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj)
        project = load_project(proj)
        fake = FakeAdapter()
        project.llm_adapter = fake
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()

        assert result.asks == [THE_ASK_ID]
        assert result.failed_asks == []
        snapshot = tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{THE_ASK_ID}.json"
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert payload["ask_id"] == THE_ASK_ID
        assert "<strong>North</strong>" in payload["html"]
        # The raw answer text bakes too, so the static client can replay the
        # answer as a typewriter before swapping in the html.
        assert payload["text"] == "**North** leads the pack."
        # The baked snapshot records the model, so a static export attributes it too.
        assert payload["model"] == "mistral-small-latest"
        assert len(fake.calls) == 1

    def test_bakes_multi_query_commentary(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj)
        _add_multi_page(proj)
        project = load_project(proj)
        fake = FakeAdapter()
        project.llm_adapter = fake
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()

        assert MULTI_ASK_ID in result.asks
        assert result.failed_asks == []
        snapshot = (
            tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{MULTI_ASK_ID}.json"
        )
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert "<strong>North</strong>" in payload["html"]
        # Both labeled result blocks reached the model in the bake.
        (prompt,) = [p for _, p in fake.calls if "Is churn driving the dip?" in p]
        assert "Result of query 'by_region':" in prompt
        assert "Result of query 'churn_by_region':" in prompt

    def test_build_without_llm_writes_notice(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj, llm_block=False)
        project = load_project(proj)
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()

        # An absent `llm:` block is expected, not a failure: the snapshot is a
        # "commentary not available" notice and the build reports no failed asks.
        assert result.asks == []
        assert result.failed_asks == []
        snapshot = tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{THE_ASK_ID}.json"
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert "error" not in payload
        assert "AI commentary is not available" in payload["notice"]


# --------------------------------------------------------------------------- #
# Chart annotations (chart_annotations.py) — validation, the restraint layer
# --------------------------------------------------------------------------- #
_LINE_CTX = ChartContext(chart_type="line", x="month", y="revenue")
_LINE_RESULT = QueryResult(
    columns=["month", "revenue"],
    rows=[
        ["Jan", 100], ["Feb", 120], ["Mar", 90],
        ["Apr", 160], ["May", 140], ["Jun", 200],
    ],
)

_BAR_CTX = ChartContext(chart_type="bar", x="region", y="total")
_BAR_RESULT = QueryResult(
    columns=["region", "total"],
    rows=[["North", 100], ["South", 200], ["West", 50]],
)

_SPLIT_CTX = ChartContext(
    chart_type="line", x="month", y="revenue", series_by="region"
)
_SPLIT_RESULT = QueryResult(
    columns=["month", "revenue", "region"],
    rows=[
        ["Jan", 100, "East"], ["Jan", 80, "West"],
        ["Feb", 120, "East"], ["Feb", 90, "West"],
    ],
)

# Combo with two y-axes at very different scales: revenue (left, 90–200) and
# orders (right, 4–9). Value-axis marks must ground against the LEFT axis only.
_COMBO_CTX = ChartContext(
    chart_type="combo",
    x="month",
    y="revenue,orders",
    extra=(("bars", "revenue"), ("lines", "orders"), ("right_axis", "orders")),
)
_COMBO_RESULT = QueryResult(
    columns=["month", "revenue", "orders"],
    rows=[
        ["Jan", 100, 5], ["Feb", 120, 6], ["Mar", 90, 4],
        ["Apr", 160, 8], ["May", 140, 7], ["Jun", 200, 9],
    ],
)

# Part-of-whole and geo vocabularies (Phase 3): pie/funnel carry `item`,
# MapChart carries `geo_item`. Pie reuses the bar-shaped result (x=label).
_PIE_CTX = ChartContext(chart_type="pie", x="region", y="total")
_FUNNEL_CTX = ChartContext(chart_type="funnel", x="stage", y="count")
_FUNNEL_RESULT = QueryResult(
    columns=["stage", "count"],
    rows=[["Visited", 1000], ["Signed up", 300], ["Paid", 40]],
)
_MAP_CTX = ChartContext(chart_type="map", x="country", y="sales")
_MAP_RESULT = QueryResult(
    columns=["country", "sales"],
    rows=[["Germany", 120], ["France", 90], ["Japan", 60]],
)

# Statistical charts (Phase 7). A candlestick grounds its value domain in the
# OHLC columns (`y` is None; the columns ride `extra`); a matrix heatmap in
# its `value` column (`y` is the row *category* column); distributions in the
# raw `y` column; a calendar in its value column.
_CANDLE_CTX = ChartContext(
    chart_type="candlestick",
    x="day",
    extra=(("close", "close"), ("high", "high"), ("low", "low"), ("open", "open")),
)
_CANDLE_RESULT = QueryResult(
    columns=["day", "open", "high", "low", "close"],
    rows=[
        ["Mon", 10, 15, 8, 12],
        ["Tue", 12, 18, 11, 17],
        ["Wed", 17, 20, 14, 15],
    ],
)

_HEATMAP_CTX = ChartContext(
    chart_type="heatmap", x="month", y="channel", extra=(("value", "downloads"),)
)
_HEATMAP_RESULT = QueryResult(
    columns=["month", "channel", "downloads"],
    rows=[["Jan", "Web", 100], ["Jan", "App", 40], ["Feb", "Web", 120]],
)

_BOX_CTX = ChartContext(chart_type="boxplot", x="category", y="amount")
_VIOLIN_CTX = ChartContext(chart_type="violin", x="category", y="amount")
_DIST_RESULT = QueryResult(
    columns=["category", "amount"],
    rows=[["A", 10], ["A", 12], ["B", 30], ["B", 5]],
)

_CAL_CTX = ChartContext(chart_type="calendar", x="day", y="count")
_CAL_RESULT = QueryResult(
    columns=["day", "count"],
    rows=[[date(2026, 1, 5), 3], [date(2026, 1, 6), 9]],
)


class TestChartAnnotationValidation:
    def test_axis_line_y_within_domain_survives(self):
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 135, "label": "Average"}],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out) == 1
        assert out[0]["id"] == "a1"
        assert out[0]["type"] == "axis_line"
        assert out[0]["axis"] == "y"
        assert out[0]["value"] == 135.0
        assert out[0]["label"] == "Average"

    def test_axis_line_slightly_above_max_survives(self):
        # A target/threshold line a bit past the observed max is legitimate —
        # the tolerance pad exists exactly for this.
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 210, "label": "Target"}],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out) == 1

    def test_axis_line_far_out_of_domain_dropped(self):
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 99999, "label": "Bogus"}],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert out == []

    def test_axis_line_x_category_must_exist(self):
        good, bad = (
            {"type": "axis_line", "axis": "x", "value": "Apr", "label": "Launch"},
            {"type": "axis_line", "axis": "x", "value": "Sept", "label": "Ghost"},
        )
        out = validate_annotations([good, bad], _LINE_RESULT, _LINE_CTX)
        assert [a["value"] for a in out] == ["Apr"]

    def test_type_not_in_chart_vocab_dropped(self):
        # `item` belongs to bar (and later pie/funnel) — not line.
        out = validate_annotations(
            [{"type": "item", "x": "Jan", "label": "January"}], _LINE_RESULT, _LINE_CTX
        )
        assert out == []
        # `point` isn't in the bar vocabulary.
        out = validate_annotations(
            [{"type": "point", "x": "North", "y": 100}], _BAR_RESULT, _BAR_CTX
        )
        assert out == []

    def test_unknown_type_and_non_dict_candidates_dropped(self):
        out = validate_annotations(
            [{"type": "banana", "label": "?"}, "not a dict", 42],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert out == []

    def test_unknown_fields_are_stripped(self):
        out = validate_annotations(
            [
                {
                    "type": "axis_line",
                    "axis": "y",
                    "value": 135,
                    "label": "Avg",
                    "color": "red",       # not ours to take
                    "onclick": "alert(1)",  # definitely not
                }
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert set(out[0]) == {"id", "type", "label", "axis", "value", "_ref"}

    def test_extremum_keeps_intent_only(self):
        # The model only saw a truncated payload — its coordinates are never
        # trusted. Only kind (+ series) survive; ECharts recomputes client-side.
        out = validate_annotations(
            [{"type": "extremum", "kind": "max", "x": "Feb", "y": 5, "label": "Peak"}],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out) == 1
        assert out[0]["kind"] == "max"
        assert "x" not in out[0] and "y" not in out[0]

    def test_extremum_bogus_kind_dropped(self):
        out = validate_annotations(
            [{"type": "extremum", "kind": "biggest"}], _LINE_RESULT, _LINE_CTX
        )
        assert out == []

    def test_series_must_name_a_real_series(self):
        # Split-column value and metric column name are both addressable…
        out = validate_annotations(
            [
                {"type": "extremum", "kind": "max", "series": "East"},
                {"type": "extremum", "kind": "min", "series": "revenue"},
                {"type": "extremum", "kind": "max", "series": "Atlantis"},
            ],
            _SPLIT_RESULT,
            _SPLIT_CTX,
        )
        assert [a.get("series") for a in out] == ["East", "revenue"]

    def test_range_orders_and_bounds_values(self):
        out = validate_annotations(
            [
                {"type": "range", "axis": "y", "from": 160, "to": 90, "label": "Band"},
                {"type": "range", "axis": "y", "from": 0, "to": 99999},
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out) == 1
        assert out[0]["from"] == 90.0 and out[0]["to"] == 160.0

    def test_range_x_categories(self):
        out = validate_annotations(
            [{"type": "range", "axis": "x", "from": "Feb", "to": "Apr", "label": "Q1"}],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out) == 1
        assert out[0]["from"] == "Feb" and out[0]["to"] == "Apr"

    def test_point_on_category_chart(self):
        good = {"type": "point", "x": "Jun", "y": 200, "label": "High"}
        bad_x = {"type": "point", "x": "Never", "y": 200}
        bad_y = {"type": "point", "x": "Jun", "y": -9999}
        out = validate_annotations([good, bad_x, bad_y], _LINE_RESULT, _LINE_CTX)
        assert len(out) == 1
        assert out[0]["x"] == "Jun" and out[0]["y"] == 200.0

    def test_scatter_x_is_numeric(self):
        ctx = ChartContext(chart_type="scatter", x="spend", y="revenue")
        result = QueryResult(
            columns=["spend", "revenue"], rows=[[10, 100], [20, 150], [30, 90]]
        )
        out = validate_annotations(
            [
                {"type": "point", "x": 20, "y": 150, "label": "Mid"},
                {"type": "point", "x": 900, "y": 150, "label": "Far off"},
                {"type": "axis_line", "axis": "x", "value": 25, "label": "Budget"},
            ],
            result,
            ctx,
        )
        assert [a["type"] for a in out] == ["point", "axis_line"]
        assert out[1]["value"] == 25.0

    def test_cap_and_sequential_ids(self):
        candidates = [
            {"type": "axis_line", "axis": "y", "value": v, "label": f"L{v}"}
            for v in (100, 120, 140, 160, 180)
        ]
        out = validate_annotations(candidates, _LINE_RESULT, _LINE_CTX)
        assert len(out) == MAX_ANNOTATIONS
        assert [a["id"] for a in out] == ["a1", "a2", "a3"]

    def test_renumber_keeps_model_side_ref(self):
        # Candidate #2 dies; survivors renumber a1..a2 but remember the model's
        # numbering ([a1], [a3] in the text) via the private _ref key.
        out = validate_annotations(
            [
                {"type": "extremum", "kind": "max", "label": "Peak"},
                {"type": "item", "x": "Jan"},  # wrong vocab for line → dropped
                {"type": "axis_line", "axis": "y", "value": 135, "label": "Avg"},
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert [(a["id"], a["_ref"]) for a in out] == [("a1", "a1"), ("a2", "a3")]

    def test_no_numeric_domain_fails_closed(self):
        result = QueryResult(columns=["month", "revenue"], rows=[["Jan", None]])
        out = validate_annotations(
            [
                {"type": "axis_line", "axis": "y", "value": 10},
                {"type": "extremum", "kind": "max"},
            ],
            result,
            _LINE_CTX,
        )
        assert out == []

    def test_label_coerced_and_capped(self):
        out = validate_annotations(
            [
                {"type": "extremum", "kind": "max", "label": "x" * 500},
                {"type": "extremum", "kind": "min", "label": {"not": "a str"}},
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        assert len(out[0]["label"]) == 80
        assert out[1]["label"] == ""

    def test_non_list_candidates_and_no_vocab(self):
        assert validate_annotations("nope", _LINE_RESULT, _LINE_CTX) == []
        radar_ctx = ChartContext(chart_type="radar", x="metric", y="score")
        assert (
            validate_annotations(
                [{"type": "item", "x": "North"}], _BAR_RESULT, radar_ctx
            )
            == []
        )

    def test_pie_item_slice_must_exist(self):
        good = {"type": "item", "x": "South", "label": "Largest slice"}
        ghost = {"type": "item", "x": "Sept", "label": "Ghost"}
        out = validate_annotations([good, ghost], _BAR_RESULT, _PIE_CTX)
        assert [a["x"] for a in out] == ["South"]
        # Pie carries only `item` — cartesian marks don't validate on it.
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 150}], _BAR_RESULT, _PIE_CTX
        )
        assert out == []

    def test_pie_item_ignores_stray_series(self):
        # A pie draws one series; a `series` the model tacked on is ignored, not
        # a reason to drop the whole mark (the client ignores it there too).
        out = validate_annotations(
            [{"type": "item", "x": "South", "series": "North", "label": "Big"}],
            _BAR_RESULT,
            _PIE_CTX,
        )
        assert [a["x"] for a in out] == ["South"]
        assert "series" not in out[0]

    def test_bar_item_keeps_valid_series_and_drops_bogus(self):
        # Bar is multi-series-capable, so the carve-out above must NOT loosen it:
        # a real series is preserved, a nonexistent one still drops the mark.
        out = validate_annotations(
            [{"type": "item", "x": "North", "series": "total", "label": "Peak"}],
            _BAR_RESULT,
            _BAR_CTX,
        )
        assert out[0]["series"] == "total"
        out = validate_annotations(
            [{"type": "item", "x": "North", "series": "ghost"}], _BAR_RESULT, _BAR_CTX
        )
        assert out == []

    def test_funnel_item_stage_must_exist(self):
        out = validate_annotations(
            [
                {"type": "item", "x": "Signed up", "label": "Biggest drop"},
                {"type": "item", "x": "Churned", "label": "Ghost"},
            ],
            _FUNNEL_RESULT,
            _FUNNEL_CTX,
        )
        assert [a["x"] for a in out] == ["Signed up"]

    def test_geo_item_location_must_exist(self):
        good = {"type": "geo_item", "name": "Germany", "label": "Top market"}
        ghost = {"type": "geo_item", "name": "Atlantis", "label": "Ghost"}
        out = validate_annotations([good, ghost], _MAP_RESULT, _MAP_CTX)
        assert len(out) == 1
        assert out[0]["name"] == "Germany"
        assert set(out[0]) == {"id", "type", "label", "name", "_ref"}
        # A map carries only `geo_item`.
        out = validate_annotations(
            [{"type": "item", "x": "Germany"}], _MAP_RESULT, _MAP_CTX
        )
        assert out == []

    def test_geo_item_accepts_id_spelling(self):
        # The prompt teaches `name`; `id` is tolerated as a fallback (the
        # Phase-4 SVG geo maps formalize normalized feature ids).
        out = validate_annotations(
            [{"type": "geo_item", "id": "France", "label": "Runner-up"}],
            _MAP_RESULT,
            _MAP_CTX,
        )
        assert len(out) == 1
        assert out[0]["name"] == "France"

    def test_date_categories_match_prompt_normalization(self):
        # Cells render into the prompt via isoformat; a candidate quoting that
        # rendering must validate against the raw date-typed column.
        result = QueryResult(
            columns=["day", "revenue"],
            rows=[[date(2026, 7, 1), 10], [date(2026, 7, 2), 30]],
        )
        ctx = ChartContext(chart_type="line", x="day", y="revenue")
        out = validate_annotations(
            [{"type": "axis_line", "axis": "x", "value": "2026-07-02"}], result, ctx
        )
        assert len(out) == 1
        assert out[0]["value"] == "2026-07-02"

    # ----- Statistical charts (Phase 7) ----------------------------------- #
    def test_candlestick_axis_line_grounds_on_ohlc_domain(self):
        # `y` is None — the value domain comes from the open/high/low/close
        # columns (8–20 here), so a price level inside it survives…
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 19, "label": "Resistance"}],
            _CANDLE_RESULT,
            _CANDLE_CTX,
        )
        assert len(out) == 1 and out[0]["value"] == 19.0
        # …and one far outside is dropped.
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 500}],
            _CANDLE_RESULT,
            _CANDLE_CTX,
        )
        assert out == []

    def test_candlestick_item_session_and_stray_series(self):
        out = validate_annotations(
            [
                {"type": "item", "x": "Tue", "series": "close", "label": "Breakout"},
                {"type": "item", "x": "Sun", "label": "Ghost"},
            ],
            _CANDLE_RESULT,
            _CANDLE_CTX,
        )
        assert [a["x"] for a in out] == ["Tue"]
        assert "series" not in out[0]  # single series: stray field ignored

    def test_candlestick_extremum_ignores_stray_series(self):
        out = validate_annotations(
            [{"type": "extremum", "kind": "max", "series": "high", "label": "Top"}],
            _CANDLE_RESULT,
            _CANDLE_CTX,
        )
        assert len(out) == 1
        assert out[0]["kind"] == "max"
        assert "series" not in out[0]

    def test_heatmap_item_addresses_a_cell_by_both_axes(self):
        good = {"type": "item", "x": "Jan", "y": "App", "label": "Cold cell"}
        bad_y = {"type": "item", "x": "Jan", "y": "Email"}
        missing_y = {"type": "item", "x": "Jan"}
        out = validate_annotations(
            [good, bad_y, missing_y], _HEATMAP_RESULT, _HEATMAP_CTX
        )
        assert len(out) == 1
        assert out[0]["x"] == "Jan" and out[0]["y"] == "App"

    def test_heatmap_extremum_grounds_on_value_column(self):
        # ctx.y is the row *category* column — the numeric domain must come
        # from the `value` extra column, or extremum could never validate.
        out = validate_annotations(
            [{"type": "extremum", "kind": "max", "label": "Hottest"}],
            _HEATMAP_RESULT,
            _HEATMAP_CTX,
        )
        assert len(out) == 1
        # No cartesian marks on a heatmap.
        out = validate_annotations(
            [{"type": "axis_line", "axis": "y", "value": 100}],
            _HEATMAP_RESULT,
            _HEATMAP_CTX,
        )
        assert out == []

    def test_boxplot_marks(self):
        out = validate_annotations(
            [
                {"type": "axis_line", "axis": "y", "value": 20, "label": "SLA"},
                {"type": "item", "x": "B", "label": "Widest spread"},
                {"type": "item", "x": "Z"},
                {"type": "extremum", "kind": "max"},  # not in the boxplot vocab
            ],
            _DIST_RESULT,
            _BOX_CTX,
        )
        assert [a["type"] for a in out] == ["axis_line", "item"]
        assert out[1]["x"] == "B"

    def test_boxplot_without_grouping_fails_x_marks_closed(self):
        # `x` is optional on BoxPlot (a single box over all rows) — with no
        # category column there is nothing an x mark could address.
        ctx = ChartContext(chart_type="boxplot", y="amount")
        out = validate_annotations(
            [
                {"type": "item", "x": "A"},
                {"type": "axis_line", "axis": "x", "value": "A"},
                {"type": "axis_line", "axis": "y", "value": 20, "label": "SLA"},
            ],
            _DIST_RESULT,
            ctx,
        )
        assert [a["type"] for a in out] == ["axis_line"]
        assert out[0]["axis"] == "y"

    def test_violin_is_value_axis_only(self):
        # The client violin draws on a synthetic numeric x axis — an x mark
        # has nothing to land on, even when the category exists in the data.
        out = validate_annotations(
            [
                {"type": "axis_line", "axis": "x", "value": "A", "label": "Nope"},
                {"type": "range", "axis": "x", "from": "A", "to": "B"},
                {"type": "axis_line", "axis": "y", "value": 25, "label": "Cap"},
                {"type": "item", "x": "A"},  # not in the violin vocab
            ],
            _DIST_RESULT,
            _VIOLIN_CTX,
        )
        assert [a["type"] for a in out] == ["axis_line"]
        assert out[0]["axis"] == "y"

    def test_calendar_item_and_extremum(self):
        out = validate_annotations(
            [
                {"type": "item", "x": "2026-01-06", "label": "Busiest day"},
                {"type": "item", "x": "2026-02-01"},
                {"type": "extremum", "kind": "max", "label": "Peak"},
            ],
            _CAL_RESULT,
            _CAL_CTX,
        )
        assert [a["type"] for a in out] == ["item", "extremum"]
        assert out[0]["x"] == "2026-01-06"


# --------------------------------------------------------------------------- #
# Annotated answer parsing: fence split, ref chips, replay-text stripping
# --------------------------------------------------------------------------- #
_ANNOTATED_REPLY = (
    "Revenue peaked in June [a1]. It dipped in March [a2].\n"
    "\n"
    "```annotations\n"
    '[{"type": "extremum", "kind": "max", "label": "June peak"},\n'
    ' {"type": "point", "x": "Mar", "y": 90, "label": "March dip"}]\n'
    "```"
)


class TestAnnotatedAnswerParsing:
    def test_split_strips_fence_and_parses_candidates(self):
        commentary, candidates = split_annotated_answer(_ANNOTATED_REPLY)
        assert "```" not in commentary
        assert "annotations" not in commentary
        assert commentary.startswith("Revenue peaked in June [a1].")
        assert [c["type"] for c in candidates] == ["extremum", "point"]

    def test_missing_fence_is_commentary_only(self):
        commentary, candidates = split_annotated_answer("Just prose, no marks.")
        assert commentary == "Just prose, no marks."
        assert candidates == []

    def test_garbled_json_fence_still_stripped(self):
        raw = "Prose first.\n\n```annotations\n[{not json%%\n```"
        commentary, candidates = split_annotated_answer(raw)
        assert commentary == "Prose first."
        assert candidates == []

    def test_non_array_json_ignored(self):
        raw = 'Prose.\n\n```annotations\n{"type": "extremum"}\n```'
        commentary, candidates = split_annotated_answer(raw)
        assert commentary == "Prose."
        assert candidates == []

    def test_multiple_fences_all_stripped_last_wins(self):
        raw = (
            "A.\n\n```annotations\n[{\"type\": \"item\", \"x\": \"old\"}]\n```\n"
            "B.\n\n```annotations\n[{\"type\": \"extremum\", \"kind\": \"max\"}]\n```"
        )
        commentary, candidates = split_annotated_answer(raw)
        assert "```" not in commentary
        assert "A." in commentary and "B." in commentary
        assert [c["type"] for c in candidates] == ["extremum"]

    def test_ordinary_code_fences_survive(self):
        raw = "Look:\n\n```sql\nSELECT 1\n```\n\nDone [a1].\n\n```annotations\n[]\n```"
        commentary, candidates = split_annotated_answer(raw)
        assert "```sql\nSELECT 1\n```" in commentary
        assert candidates == []

    def test_strip_ref_tokens_tidies_replay_text(self):
        text = strip_ref_tokens("Revenue peaked in June [a1]. Dip [a2] happened.")
        assert text == "Revenue peaked in June. Dip happened."

    def test_inject_refs_replaces_surviving_and_strips_orphans(self):
        annotations = validate_annotations(
            [
                {"type": "extremum", "kind": "max", "label": "June peak"},
                {"type": "item", "x": "nope"},  # dies (wrong vocab for line)
                {"type": "axis_line", "axis": "y", "value": 135, "label": "Avg"},
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        html = "<p>Peak [a1]. Ghost [a2]. Average [a3].</p>"
        out = inject_refs(html, annotations)
        assert '<abbr class="dashdown-anno-ref" data-anno-id="a1"' in out
        # The third model-side ref maps onto the renumbered second survivor.
        assert 'data-anno-id="a2"' in out
        assert "[a2]" not in out and "Ghost." in out  # orphan stripped, tidied
        assert ">1</abbr>" in out and ">2</abbr>" in out
        assert 'title="June peak"' in out and 'title="Avg"' in out
        # The private _ref key was consumed — the shipped payload is clean.
        assert all("_ref" not in a for a in annotations)

    def test_inject_refs_escapes_labels(self):
        # A hostile label can't break out of the title attribute — the model's
        # text never becomes markup.
        annotations = validate_annotations(
            [
                {
                    "type": "extremum",
                    "kind": "max",
                    "label": '"><script>alert(1)</script>',
                }
            ],
            _LINE_RESULT,
            _LINE_CTX,
        )
        out = inject_refs("<p>Peak [a1].</p>", annotations)
        assert "<script>" not in out
        assert "&quot;&gt;&lt;script&gt;" in out

    def test_generate_answer_end_to_end_with_chart_context(self):
        fake = FakeAdapter(reply=_ANNOTATED_REPLY)
        ask = AskDef(
            id="x",
            queries=(("q", "main"),),
            prompt="Explain this chart.",
            chart_context=_LINE_CTX,
        )
        html, text, annotations = generate_answer(ask, [_LINE_RESULT], fake)
        # Annotations validated + renumbered; fence and tokens gone everywhere.
        assert [a["id"] for a in annotations] == ["a1", "a2"]
        assert annotations[0]["kind"] == "max"
        assert annotations[1]["x"] == "Mar"
        assert "```" not in html and "```" not in text
        assert "dashdown-anno-ref" in html
        assert "[a1]" not in text and "[a1]" not in html
        assert text.startswith("Revenue peaked in June.")
        # The prompt taught the protocol, grounded in the actual domains.
        _, prompt = fake.calls[0]
        assert "```annotations" in prompt
        assert "X categories: Jan, Feb, Mar, Apr, May, Jun" in prompt
        assert "Y values range from 90 to 200" in prompt

    def test_generate_answer_without_fence_degrades_to_commentary(self):
        fake = FakeAdapter(reply="Nothing remarkable here.")
        ask = AskDef(
            id="x",
            queries=(("q", "main"),),
            prompt="Explain.",
            chart_context=_LINE_CTX,
        )
        html, text, annotations = generate_answer(ask, [_LINE_RESULT], fake)
        assert annotations == []
        assert "Nothing remarkable here." in html
        assert text == "Nothing remarkable here."


class TestAnnotationInstructions:
    def test_lists_only_the_charts_vocabulary(self):
        text = annotation_instructions(_BAR_CTX, _BAR_RESULT)
        assert '"type": "item"' in text  # bar has item…
        assert '"type": "point"' not in text  # …but not point
        assert '"series": "<series>" (optional)' in text  # bar item keeps series
        assert "at most 3 annotations" in text
        assert "empty list is the right answer" in text
        assert "X categories: North, South, West" in text
        assert "Y values range from 50 to 200" in text

    def test_series_names_listed_when_split(self):
        text = annotation_instructions(_SPLIT_CTX, _SPLIT_RESULT)
        assert "Series names: East, West, revenue" in text

    def test_scatter_gets_numeric_x_domain(self):
        ctx = ChartContext(chart_type="scatter", x="spend", y="revenue")
        result = QueryResult(
            columns=["spend", "revenue"], rows=[[10, 100], [30, 90]]
        )
        text = annotation_instructions(ctx, result)
        assert "X values range from 10 to 30" in text
        assert "X categories" not in text

    def test_combo_grounds_y_on_left_axis_and_omits_point(self):
        text = annotation_instructions(_COMBO_CTX, _COMBO_RESULT)
        # The reported Y domain is the LEFT axis (revenue 90–200), NOT the merged
        # bars+lines domain (which would start at orders' 4) — so the model
        # grounds threshold/range marks against the axis they actually draw on.
        assert "Y values range from 90 to 200" in text
        # Combo dropped the free-coordinate point from its vocabulary.
        assert '"type": "point"' not in text
        assert '"type": "extremum"' in text

    def test_pie_lists_item_only(self):
        text = annotation_instructions(_PIE_CTX, _BAR_RESULT)
        assert '"type": "item"' in text
        assert '"type": "extremum"' not in text
        assert '"type": "axis_line"' not in text
        assert "X categories: North, South, West" in text
        # A pie draws one series, so its item shape omits the `series` field.
        assert '"series"' not in text

    def test_map_speaks_locations_and_lists_geo_item(self):
        text = annotation_instructions(_MAP_CTX, _MAP_RESULT)
        assert '"type": "geo_item"' in text
        assert '"type": "item"' not in text
        # The grounding lines speak geography, not axes.
        assert "Location names: Germany, France, Japan" in text
        assert "location column 'country'" in text
        assert "Values range from 60 to 120" in text
        assert "X categories" not in text

    def test_candlestick_speaks_prices_and_sessions(self):
        text = annotation_instructions(_CANDLE_CTX, _CANDLE_RESULT)
        # The value domain spans the OHLC columns, labeled as prices.
        assert "Prices range from 8 to 20" in text
        assert "X categories: Mon, Tue, Wed" in text
        assert "marks that session" in text
        assert "highest high (max) or lowest low (min)" in text
        # Single series — the item shape never advertises `series`.
        assert '"series"' not in text

    def test_heatmap_lists_both_category_axes_and_cell_values(self):
        text = annotation_instructions(_HEATMAP_CTX, _HEATMAP_RESULT)
        assert "X categories: Jan, Feb" in text
        assert "Y categories: Web, App" in text
        assert "Cell values range from 40 to 120" in text
        assert '"y": "<y category>"' in text  # item addresses a cell
        assert "outlines the highest/lowest cell" in text
        assert '"type": "axis_line"' not in text

    def test_violin_offers_value_axis_marks_only(self):
        text = annotation_instructions(_VIOLIN_CTX, _DIST_RESULT)
        assert '"axis": "y"' in text
        assert '"axis": "x" or "y"' not in text
        assert "Y values range from 5 to 30" in text

    def test_calendar_speaks_days(self):
        text = annotation_instructions(_CAL_CTX, _CAL_RESULT)
        assert "outlines that day" in text
        assert "outlines the highest/lowest day" in text
        assert "Cell values range from 3 to 9" in text
        assert "X categories: 2026-01-05, 2026-01-06" in text


# --------------------------------------------------------------------------- #
# Combo dual-axis: value marks scope to the left axis; free point is out
# --------------------------------------------------------------------------- #
class TestComboAnnotationAxes:
    def test_combo_drops_free_point(self):
        # Combo carries two y-axes at different scales, so a free-coordinate
        # point (an explicit x+y) can't be grounded — it's out of the vocabulary.
        out = validate_annotations(
            [{"type": "point", "x": "Jun", "y": 200, "label": "Peak"}],
            _COMBO_RESULT,
            _COMBO_CTX,
        )
        assert out == []

    def test_combo_axis_line_validates_against_left_axis(self):
        # 150 sits in the LEFT (revenue) domain → survives; 7 is a right-axis
        # (orders) magnitude far below the left domain → dropped, even though a
        # naive merged bars+lines domain would have waved it through.
        out = validate_annotations(
            [
                {"type": "axis_line", "axis": "y", "value": 150, "label": "Left"},
                {"type": "axis_line", "axis": "y", "value": 7, "label": "Right"},
            ],
            _COMBO_RESULT,
            _COMBO_CTX,
        )
        assert [a["value"] for a in out] == [150.0]

    def test_combo_extremum_on_right_axis_series_survives(self):
        # extremum carries no coordinate — ECharts recomputes it per series on
        # that series' own axis — so a right-axis line is still a valid target.
        out = validate_annotations(
            [{"type": "extremum", "kind": "max", "series": "orders"}],
            _COMBO_RESULT,
            _COMBO_CTX,
        )
        assert len(out) == 1
        assert out[0]["series"] == "orders"


# --------------------------------------------------------------------------- #
# chart_context in the ask registry — id compat and cache busting
# --------------------------------------------------------------------------- #
class TestChartContextAskIds:
    def test_plain_ask_id_byte_compat(self):
        # Golden value: the exact pre-chart_context hash for this input. Plain
        # asks must keep byte-identical ids across the feature landing, or
        # every existing answer cache / baked snapshot would silently bust.
        assert ask_id([("sales", "main")], "What changed?") == "86260ea3e831e26a"
        assert (
            ask_id([("sales", "main")], "What changed?", chart_context=None)
            == "86260ea3e831e26a"
        )

    def test_chart_context_joins_hash_only_when_set(self):
        base = ask_id([("sales", "main")], "Explain.")
        ctx = ChartContext(chart_type="bar", x="region", y="total")
        assert ask_id([("sales", "main")], "Explain.", chart_context=ctx) != base
        # Deterministic: the same shape hashes the same…
        assert ask_id(
            [("sales", "main")], "Explain.", chart_context=ctx
        ) == ask_id(
            [("sales", "main")],
            "Explain.",
            chart_context=ChartContext(chart_type="bar", x="region", y="total"),
        )
        # …and a reshaped chart must mint a fresh id (busts its explain cache).
        assert ask_id(
            [("sales", "main")],
            "Explain.",
            chart_context=ChartContext(chart_type="bar", x="region", y="profit"),
        ) != ask_id([("sales", "main")], "Explain.", chart_context=ctx)

    def test_register_threads_chart_context(self):
        ctx = ChartContext(chart_type="line", x="month", y="revenue")
        d = register_ask_def([("q", "main")], "Explain.", chart_context=ctx)
        assert d.chart_context == ctx
        assert get_ask_def(d.id).chart_context == ctx

    def test_build_chart_context_only_for_vocabulary_types(self):
        assert build_chart_context("radar", x="metric", y="score") is None
        assert build_chart_context("gauge") is None
        ctx = build_chart_context("bar", x="region", y="total", horizontal=True)
        assert ctx == ChartContext(
            chart_type="bar", x="region", y="total", horizontal=True
        )
        # Every vocabulary type builds a context (the two tables stay in sync).
        for chart_type in ANNOTATION_VOCAB:
            assert build_chart_context(chart_type, x="x", y="y") is not None

    def test_faceted_pie_has_no_context(self):
        # A pie with `series=` renders small multiples — the client patches
        # series indexes per facet, so an `item` mark can't address one pie.
        assert build_chart_context("pie", x="region", y="total") is not None
        assert (
            build_chart_context("pie", x="region", y="total", series_by="quarter")
            is None
        )


# --------------------------------------------------------------------------- #
# chart_context threading from the chart components
# --------------------------------------------------------------------------- #
class TestChartExplainContext:
    def test_bar_explain_carries_context_and_bigger_row_cap(self):
        ask = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        assert ask.chart_context == ChartContext(
            chart_type="bar", x="region", y="total"
        )
        # Annotation-bearing asks see more rows (validation runs on the full
        # result; the model shouldn't ground candidates in a truncated view).
        assert ask.max_rows == DEFAULT_EXPLAIN_MAX_ROWS

    def test_max_rows_attr_overrides(self):
        source = EXPLAIN_PAGE.replace(" explain", " explain max_rows=25")
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.max_rows == 25

    def test_shape_flags_thread_and_bust_the_id(self):
        base = render_page(EXPLAIN_PAGE, connectors={}).ask_defs[0]
        source = EXPLAIN_PAGE.replace(" explain", " horizontal explain")
        flipped = render_page(source, connectors={}).ask_defs[0]
        assert flipped.chart_context.horizontal is True
        assert flipped.id != base.id  # reshaped chart ⇒ fresh answer

    def test_series_split_threads(self):
        source = EXPLAIN_PAGE.replace(" explain", ' series="channel" explain')
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context.series_by == "channel"

    def test_unvocabularied_chart_stays_commentary_only(self):
        source = EXPLAIN_PAGE.replace("<BarChart", "<TreemapChart")
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context is None
        # …and keeps the plain-<Ask /> row cap (its id must not move).
        assert ask.max_rows == DEFAULT_MAX_ROWS

    def test_pie_and_funnel_explain_carry_context(self):
        for tag, chart_type in (("PieChart", "pie"), ("FunnelChart", "funnel")):
            source = EXPLAIN_PAGE.replace("<BarChart", f"<{tag}")
            ask = render_page(source, connectors={}).ask_defs[0]
            assert ask.chart_context == ChartContext(
                chart_type=chart_type, x="region", y="total"
            )
            assert ask.max_rows == DEFAULT_EXPLAIN_MAX_ROWS

    def test_faceted_pie_explain_stays_commentary_only(self):
        source = EXPLAIN_PAGE.replace("<BarChart", "<PieChart").replace(
            " explain", ' series="channel" explain'
        )
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context is None
        assert ask.max_rows == DEFAULT_MAX_ROWS

    def test_map_explain_context_uses_location_value_aliases(self):
        source = """# T

:::query name=by_country connector=main
SELECT country, SUM(amount) AS sales FROM sales GROUP BY country
:::

<MapChart data={by_country} location="country" value="sales" explain />
"""
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context == ChartContext(
            chart_type="map", x="country", y="sales"
        )
        assert ask.max_rows == DEFAULT_EXPLAIN_MAX_ROWS

    def test_live_query_chart_is_commentary_only(self):
        # Decided: a chart on a `live` query gets no chart_context — the data
        # changes under the marks every poll interval.
        source = EXPLAIN_PAGE.replace(
            ":::query name=by_region connector=main",
            ":::query name=by_region connector=main live",
        )
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context is None
        assert ask.max_rows == DEFAULT_MAX_ROWS

    def test_combo_context_carries_the_series_mix(self):
        source = """# T

:::query name=q connector=main
SELECT month, revenue, orders FROM t
:::

<ComboChart data={q} x="month" bars="revenue" lines="orders" right_axis="orders" explain />
"""
        ask = render_page(source, connectors={}).ask_defs[0]
        ctx = ask.chart_context
        assert ctx.chart_type == "combo"
        assert ctx.x == "month"
        assert ctx.y == "revenue,orders"
        assert dict(ctx.extra) == {
            "bars": "revenue",
            "lines": "orders",
            "right_axis": "orders",
        }

    def test_candlestick_context_carries_the_ohlc_columns(self):
        source = """# T

:::query name=prices connector=main
SELECT day, o, h, l, c FROM prices
:::

<CandlestickChart data={prices} x="day" open="o" high="h" low="l" close="c" explain />
"""
        ask = render_page(source, connectors={}).ask_defs[0]
        ctx = ask.chart_context
        assert ctx.chart_type == "candlestick"
        assert ctx.x == "day" and ctx.y is None
        assert dict(ctx.extra) == {"open": "o", "high": "h", "low": "l", "close": "c"}
        assert ask.max_rows == DEFAULT_EXPLAIN_MAX_ROWS

    def test_heatmap_context_value_column_joins_the_id(self):
        source = """# T

:::query name=grid connector=main
SELECT month, channel, downloads, sessions FROM t
:::

<HeatmapChart data={grid} x="month" y="channel" value="downloads" explain />
"""
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context.chart_type == "heatmap"
        assert dict(ask.chart_context.extra) == {"value": "downloads"}
        # The value column shapes grounding and validation — changing it must
        # mint a new ask id (busting the cached answer is correct there).
        other = render_page(
            source.replace('value="downloads"', 'value="sessions"'), connectors={}
        ).ask_defs[0]
        assert other.id != ask.id

    def test_boxplot_violin_and_calendar_carry_context(self):
        for tag, chart_type in (
            ("BoxPlot", "boxplot"),
            ("Violin", "violin"),
        ):
            source = EXPLAIN_PAGE.replace(
                '<BarChart data={by_region} x="region" y="total"',
                f'<{tag} data={{by_region}} x="region" y="total"',
            )
            ask = render_page(source, connectors={}).ask_defs[0]
            assert ask.chart_context == ChartContext(
                chart_type=chart_type, x="region", y="total"
            )
        source = EXPLAIN_PAGE.replace(
            '<BarChart data={by_region} x="region" y="total"',
            '<CalendarHeatmap data={by_region} date="region" value="total"',
        )
        ask = render_page(source, connectors={}).ask_defs[0]
        assert ask.chart_context == ChartContext(
            chart_type="calendar", x="region", y="total"
        )


# --------------------------------------------------------------------------- #
# Endpoint behavior for annotation-bearing (chart-context) asks
# --------------------------------------------------------------------------- #
EXPLAIN_CHART_PAGE = """# Sales

:::query name=by_region connector=main
SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC
:::

<BarChart data={by_region} x="region" y="total" title="Revenue by region" explain />
"""

# Two grounded candidates + one hallucinated value the validator must kill.
_EXPLAIN_REPLY = (
    "South leads [a1]. North holds second [a2]. Impossible claim [a3].\n"
    "\n"
    "```annotations\n"
    '[{"type": "extremum", "kind": "max", "label": "South peak"},\n'
    ' {"type": "item", "x": "North", "label": "North bar"},\n'
    ' {"type": "axis_line", "axis": "y", "value": 999999, "label": "Bogus"}]\n'
    "```"
)


def _explain_client(tmp_path, fake):
    proj = tmp_path / "proj"
    proj.mkdir()
    _make_project(proj)
    (proj / "pages" / "index.md").write_text(EXPLAIN_CHART_PAGE, encoding="utf-8")
    app = create_app(proj)
    app.state.project.llm_adapter = fake
    client = TestClient(app)
    assert client.get("/").status_code == 200
    asks = [a for a in _ask_def_cache.values() if a.chart_context is not None]
    assert len(asks) == 1
    return client, fake, asks[0]


class TestAnnotatedAskEndpoint:
    def test_stream_param_ignored_returns_json_with_annotations(self, tmp_path):
        client, fake, ask = _explain_client(tmp_path, FakeAdapter(reply=_EXPLAIN_REPLY))
        # ask.js always sends _stream=1 — a chart-context ask must answer with
        # the JSON payload anyway (SSE would type the fence out to the viewer).
        r = client.get(f"/_dashdown/api/ask/{ask.id}?_stream=1")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert body["cached"] is False
        # The hallucinated axis_line died; survivors renumbered a1..a2.
        assert [a["id"] for a in body["annotations"]] == ["a1", "a2"]
        assert body["annotations"][0]["type"] == "extremum"
        assert body["annotations"][1] == {
            "id": "a2", "type": "item", "label": "North bar", "x": "North",
        }
        # Chips in the html; orphan ref stripped; fence gone everywhere.
        assert 'data-anno-id="a1"' in body["html"]
        assert 'data-anno-id="a2"' in body["html"]
        assert "[a3]" not in body["html"] and "```" not in body["html"]
        # Replay text ships clean (typewriter shows plain text, chips can't).
        assert body["text"].startswith("South leads. North holds second.")
        assert "```" not in body["text"]

    def test_cache_hit_replays_annotations(self, tmp_path):
        client, fake, ask = _explain_client(tmp_path, FakeAdapter(reply=_EXPLAIN_REPLY))
        first = client.get(f"/_dashdown/api/ask/{ask.id}").json()
        second = client.get(f"/_dashdown/api/ask/{ask.id}").json()
        assert second["cached"] is True
        assert second["annotations"] == first["annotations"]
        assert len(fake.calls) == 1

    def test_prompt_carries_annotation_protocol_and_domains(self, tmp_path):
        client, fake, ask = _explain_client(tmp_path, FakeAdapter(reply=_EXPLAIN_REPLY))
        client.get(f"/_dashdown/api/ask/{ask.id}")
        _, prompt = fake.calls[0]
        assert "```annotations" in prompt
        assert "X categories: South, North, West" in prompt  # ORDER BY total DESC
        assert "Y values range from 50 to 200" in prompt

    def test_plain_ask_stream_untouched(self, tmp_path):
        # The SSE path stays exactly as-is for commentary-only asks.
        client, fake = _client_with_fake(tmp_path, fake=StreamingFakeAdapter())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}?_stream=1")
        assert r.headers["content-type"].startswith("text/event-stream")


class TestAnnotatedStaticBake:
    def test_build_bakes_annotated_explain_snapshot(self, tmp_path):
        # The requested static parity, end to end: a chart with `explain` in a
        # static export renders its button + panel, and the baked
        # _ask/{id}.json carries the sanitized html (ref chips included), the
        # replayable text, AND the validated annotations — everything ask.js's
        # static branch needs on first click.
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj)
        (proj / "pages" / "index.md").write_text(EXPLAIN_CHART_PAGE, encoding="utf-8")
        project = load_project(proj)
        project.llm_adapter = FakeAdapter(reply=_EXPLAIN_REPLY)
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()

        assert result.failed_asks == []
        assert len(result.asks) == 1
        page = (tmp_path / "dist" / "index.html").read_text(encoding="utf-8")
        assert "dashdown-explain-btn" in page
        assert "dashdown-explain-panel" in page

        snapshot = (
            tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{result.asks[0]}.json"
        )
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        # The hallucinated candidate died at build time too; survivors ship
        # renumbered — identical validation to the live endpoint.
        assert [a["id"] for a in payload["annotations"]] == ["a1", "a2"]
        assert payload["annotations"][0]["kind"] == "max"
        assert 'data-anno-id="a1"' in payload["html"]
        assert "```" not in payload["html"] and "```" not in payload["text"]
        assert "[a3]" not in payload["html"]
        assert payload["text"].startswith("South leads. North holds second.")

    def test_bake_payload_shape_matches_endpoint(self):
        # _export_ask writes what generate_answer returns; lock the shape.
        html, text, annotations = generate_answer(
            AskDef(
                id="x",
                queries=(("q", "main"),),
                prompt="Explain.",
                chart_context=_BAR_CTX,
            ),
            [_BAR_RESULT],
            FakeAdapter(reply=_EXPLAIN_REPLY),
        )
        assert [a["id"] for a in annotations] == ["a1", "a2"]
        assert "dashdown-anno-ref" in html
