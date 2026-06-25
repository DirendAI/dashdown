"""Tests for the <Ask /> LLM commentary feature (Stage 11).

Layers: `llm:` config parsing, prompt registration via render_page, the
payload row cap, the /_dashdown/api/ask endpoint with a fake adapter
(including answer caching), and static-build baking.
"""
import json

import pytest
from fastapi.testclient import TestClient

from dashdown.build import _build
from dashdown.data.base import QueryResult
from dashdown.llm import (
    DEFAULT_ANSWER_TTL,
    DEFAULT_MAX_ROWS,
    AnthropicAdapter,
    AskDef,
    LLMAdapter,
    LLMConfig,
    OpenAIAdapter,
    OpenRouterAdapter,
    _answer_cache,
    _ask_def_cache,
    ask_id,
    cache_answer,
    create_adapter,
    format_result_for_llm,
    generate_answer_html,
    get_ask_def,
    get_cached_answer,
    known_providers,
    parse_llm_config,
    register_ask_def,
    relevant_params,
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


# --------------------------------------------------------------------------- #
# Provider adapters (request mapping, with the SDK client faked out)
# --------------------------------------------------------------------------- #
class _FakeAnthropicClient:
    """Mimics anthropic.Anthropic — records create() kwargs, returns blocks."""

    class _Block:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.create_kwargs = kwargs
            return type("R", (), {"content": [_FakeAnthropicClient._Block("**Hi**")]})

    def __init__(self):
        self.create_kwargs = None
        self.messages = _FakeAnthropicClient._Messages(self)


class _FakeOpenAIClient:
    """Mimics openai.OpenAI — records create() kwargs, returns a choice."""

    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.create_kwargs = kwargs
            msg = type("M", (), {"content": "**Hi**"})
            choice = type("C", (), {"message": msg})
            return type("R", (), {"choices": [choice]})

    def __init__(self):
        self.create_kwargs = None
        self.chat = type("Chat", (), {"completions": _FakeOpenAIClient._Completions(self)})


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


# --------------------------------------------------------------------------- #
# Prompt registry
# --------------------------------------------------------------------------- #
class TestAskRegistry:
    def test_id_is_deterministic(self):
        a = ask_id("sales", "main", "What changed?")
        b = ask_id("sales", "main", "What changed?")
        assert a == b

    def test_id_varies_with_inputs(self):
        base = ask_id("sales", "main", "What changed?")
        assert ask_id("sales", "main", "Other prompt") != base
        assert ask_id("other", "main", "What changed?") != base
        assert ask_id("sales", "warehouse", "What changed?") != base
        # max_rows changes the payload the model sees → must bust the cache id.
        assert ask_id("sales", "main", "What changed?", max_rows=5) != base

    def test_register_and_lookup(self):
        d = register_ask_def("sales", "main", "Why?")
        assert get_ask_def(d.id) == d
        assert get_ask_def("nope") is None

    def test_register_defaults(self):
        d = register_ask_def("sales", "main", "Why?")
        assert d.max_rows == DEFAULT_MAX_ROWS
        assert d.cache_ttl == DEFAULT_ANSWER_TTL


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


class TestAskComponent:
    def test_registers_prompt_and_emits_placeholder(self):
        rendered = render_page(ASK_PAGE, connectors={})
        assert 'data-async-component="ask"' in rendered.body_html
        assert len(rendered.ask_defs) == 1
        ask = rendered.ask_defs[0]
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
        ask = AskDef(id="x", query_name="q", connector="main", prompt="Why?", max_rows=5)
        result = QueryResult(columns=["a"], rows=[[1]])
        html = generate_answer_html(ask, result, fake)
        assert "<strong>North</strong>" in html
        # Prompt + data both reached the model.
        system, prompt = fake.calls[0]
        assert "Why?" in prompt
        assert "a (int)" in prompt

    def test_llm_html_output_is_escaped(self):
        # A prompt-injected model can't smuggle markup into the page.
        html = render_markdown_text("<script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# Answer cache helpers
# --------------------------------------------------------------------------- #
class TestAnswerCache:
    def test_relevant_params_filters_to_sql(self):
        sql = "SELECT * FROM t WHERE r = '${region}'"
        assert relevant_params(sql, {"region": "N", "other": "x"}) == {"region": "N"}

    def test_roundtrip_and_expiry(self):
        cache_answer("id1", {"a": "1"}, "<p>hi</p>", ttl=60)
        assert get_cached_answer("id1", {"a": "1"}) == "<p>hi</p>"
        assert get_cached_answer("id1", {"a": "2"}) is None
        cache_answer("id2", {}, "<p>old</p>", ttl=-1)  # already expired
        assert get_cached_answer("id2", {}) is None


# --------------------------------------------------------------------------- #
# Endpoint integration
# --------------------------------------------------------------------------- #
def _make_project(root, llm_block=True):
    (root / "pages").mkdir()
    (root / "data").mkdir()
    yaml = "title: Ask Test\n"
    if llm_block:
        yaml += "llm:\n  provider: mistral\n  api_key: dummy\n"
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
    "by_region",
    "main",
    "Which region leads and why?",
    max_rows=2,  # set on the tag in ASK_PAGE; part of the id hash
)


class TestAskEndpoint:
    def test_generates_and_caches(self, tmp_path):
        client, fake = _client_with_fake(tmp_path, fake=FakeAdapter())

        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 200
        body = r.json()
        assert body["cached"] is False
        assert "<strong>North</strong>" in body["html"]
        assert len(fake.calls) == 1

        # Second identical request answers from the cache — no new LLM call.
        r2 = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r2.json()["cached"] is True
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

    def test_unconfigured_llm_503s(self, tmp_path):
        client, _ = _client_with_fake(tmp_path, llm_block=False)
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 503
        assert "llm" in r.json()["detail"].lower()

    def test_provider_error_502s(self, tmp_path):
        class BoomAdapter(FakeAdapter):
            def complete(self, system, prompt):
                raise RuntimeError("rate limited")

        client, _ = _client_with_fake(tmp_path, fake=BoomAdapter())
        r = client.get(f"/_dashdown/api/ask/{THE_ASK_ID}")
        assert r.status_code == 502
        assert "rate limited" in r.json()["detail"]


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
        assert len(fake.calls) == 1

    def test_build_without_llm_writes_error(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _make_project(proj, llm_block=False)
        project = load_project(proj)
        try:
            result = _build(project, tmp_path / "dist")
        finally:
            project.close()

        assert result.asks == []
        assert len(result.failed_asks) == 1
        snapshot = tmp_path / "dist" / "_dashdown" / "data" / "_ask" / f"{THE_ASK_ID}.json"
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert "error" in payload
