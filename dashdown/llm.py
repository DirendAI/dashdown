"""LLM provider adapter + the ``<Ask />`` prompt registry.

Configured under an ``llm:`` block in ``dashdown.yaml``:

    llm:
      provider: mistral
      api_key: ${MISTRAL_API_KEY}    # ${VAR} reads from the environment
      model: mistral-small-latest    # optional (this is the mistral default)

Supported providers: ``mistral``, ``anthropic`` (Claude), ``openai``, and
``openrouter`` (OpenRouter's OpenAI-compatible gateway). Each has a sensible
default model except ``openrouter`` — which routes to many upstream models, so
``llm.model`` is required there (a fully-qualified slug like
``anthropic/claude-3.5-sonnet``). The default model is configurable for all:
the framework picks the most capable model, but a high-volume project will
usually want to pin a cheaper/faster one via ``llm.model`` (every cache-miss
is billed).

No ``llm:`` block (the default) leaves the feature off; ``<Ask />`` blocks
then report that no provider is configured instead of failing the page. A
*misconfigured* block (unset ``${API_KEY}`` env var, unknown provider, …)
behaves the same way: ``load_project`` logs the problem and disables the
feature (``LLMConfig.error`` carries the reason for the ask cards) rather
than refusing to serve or build.
Per-ask knobs (``max_rows``, ``cache_ttl``) are component attributes with
defaults below — mirroring how ``cache_ttl`` works on ``:::query`` blocks —
not global config.

Provider SDKs are optional extras mirroring the connector packaging
(``pip install 'dashdown-md[mistral|anthropic|openai|openrouter]'``); the import
happens lazily on first use and a missing dependency raises a friendly install
hint.

This module also owns the ask-prompt registry. ``<Ask />`` placeholders carry
only an opaque id; the public ``/_dashdown/api/ask/{id}`` endpoint resolves it
here, so it can never be fed an arbitrary prompt. Ids are a *deterministic*
hash of (connector, query, prompt) — unlike a per-render uuid, the same
authored block keeps the same id across page renders and server restarts,
which is what lets the answer cache actually absorb repeat page loads.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from dashdown.data.base import QueryResult
from dashdown.render.markdown import render_markdown_text

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"^\$\{(\w+)\}$")
_PARAM_RE = re.compile(r"\$\{(\w+)\}")

DEFAULT_MAX_ROWS = 50
# Generous by design: every cache miss is an LLM bill, and commentary on a
# fixed query result rarely changes meaningfully within the hour.
DEFAULT_ANSWER_TTL = 3600


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
@dataclass
class LLMConfig:
    """Resolved ``llm:`` settings. The api_key is already env-expanded.

    Deliberately provider-only (no feature knobs): the adapter this configures
    is a general LLM gateway that future features can share — anything
    specific to one consumer (like ``<Ask />``'s ``max_rows``/``cache_ttl``)
    belongs on that consumer.

    ``error`` carries the reason a present-but-broken ``llm:`` block was
    disabled (e.g. an unset ``${API_KEY}`` env var). Unlike ``auth:``, a bad
    llm block must NOT refuse to start the server — the feature just turns
    off and every ``<Ask />`` card explains why (see ``load_project``).
    """

    provider: str = "none"
    model: str | None = None
    api_key: str = ""
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.provider != "none"


def _resolve_secret(value: Any) -> str:
    """Expand a ``${VAR}`` reference from the environment, else return as-is."""
    s = str(value)
    m = _ENV_RE.match(s.strip())
    if m:
        env_val = os.environ.get(m.group(1))
        if env_val is None:
            raise ValueError(
                f"llm config references environment variable {m.group(1)!r}, "
                "which is not set"
            )
        return env_val
    return s


def parse_llm_config(raw: dict | None) -> LLMConfig:
    """Build an :class:`LLMConfig` from the ``llm`` block of dashdown.yaml.

    Raises ``ValueError`` on misconfiguration — but unlike ``auth:``, the
    caller (``load_project``) catches it and degrades to a *disabled* config
    carrying the message, so `dashdown serve` / `dashdown build` still run
    without the key and each ``<Ask />`` card explains why commentary is off.
    (Auth stays fail-hard: a half-configured guard must never start open.)
    """
    if not raw:
        return LLMConfig()
    if not isinstance(raw, dict):
        raise ValueError("llm config must be a mapping (provider / api_key keys)")

    provider = str(raw.get("provider", "none")).lower()
    if provider == "none":
        return LLMConfig()
    if provider not in _PROVIDERS:
        raise ValueError(
            f"unknown llm.provider {provider!r} (expected one of {known_providers()})"
        )

    api_key = _resolve_secret(raw.get("api_key", ""))
    if not api_key:
        raise ValueError(f"llm.provider {provider!r} requires an api_key")

    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("llm.model must be a non-empty string")
    if model is None and _PROVIDERS[provider].DEFAULT_MODEL is None:
        # e.g. openrouter — routes to many models, so there's no default to fall
        # back to. Fail at startup (same policy as the rest of this function).
        raise ValueError(
            f"llm.provider {provider!r} has no default model — set llm.model "
            "(e.g. a fully-qualified slug like 'anthropic/claude-3.5-sonnet')"
        )

    for moved in ("max_rows", "cache_ttl"):
        if moved in raw:
            raise ValueError(
                f"llm.{moved} moved to the <Ask /> component — set it as a "
                f"component attribute (e.g. <Ask {moved}=… />)"
            )

    return LLMConfig(
        provider=provider,
        model=model.strip() if isinstance(model, str) else None,
        api_key=api_key,
    )


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #
class LLMAdapter(ABC):
    """One chat completion — the whole interface ``<Ask />`` needs."""

    #: Model used when ``llm.model`` is unset. ``None`` means the provider has
    #: no sensible default and the user must set ``llm.model`` (enforced at
    #: config load — see ``parse_llm_config``).
    DEFAULT_MODEL: str | None = None

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    def complete(self, system: str, prompt: str) -> str:  # pragma: no cover
        """Return the model's text answer for a system + user message pair."""
        ...

    def stream_complete(self, system: str, prompt: str) -> Iterator[str]:
        """Yield the answer incrementally (the SSE "typing" path).

        The base implementation degrades to a single chunk via
        :meth:`complete`, so an adapter without native streaming still works
        behind the streaming endpoint; the built-in providers all override
        with their SDK's native stream.
        """
        yield self.complete(system, prompt)


class MistralAdapter(LLMAdapter):
    """Mistral chat completions via the official ``mistralai`` SDK
    (``pip install 'dashdown-md[mistral]'``)."""

    DEFAULT_MODEL = "mistral-small-latest"

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = None  # lazy: SDK import + client on first call

    def _get_client(self):
        if self._client is None:
            try:
                # SDK v1.x exports the client at top level; v2.x turned
                # `mistralai` into a namespace package and moved it.
                try:
                    from mistralai import Mistral
                except ImportError:
                    from mistralai.client import Mistral
            except ImportError as e:
                raise ImportError(
                    "LLM provider 'mistral' requires the mistralai package. "
                    "Install it with: pip install 'dashdown-md[mistral]'  "
                    f"(underlying error: {e})"
                ) from e
            self._client = Mistral(api_key=self.config.api_key)
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        client = self._get_client()
        response = client.chat.complete(
            model=self.config.model or self.DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        content = response.choices[0].message.content
        if isinstance(content, list):  # SDK v2 may return content chunks
            content = "".join(getattr(chunk, "text", "") or "" for chunk in content)
        return content or ""

    def stream_complete(self, system: str, prompt: str) -> Iterator[str]:
        client = self._get_client()
        stream = client.chat.stream(
            model=self.config.model or self.DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        for event in stream:
            if not event.data.choices:
                continue
            content = event.data.choices[0].delta.content
            if isinstance(content, list):  # SDK v2 may return content chunks
                content = "".join(getattr(c, "text", "") or "" for c in content)
            if content:
                yield content


class AnthropicAdapter(LLMAdapter):
    """Anthropic (Claude) messages via the official ``anthropic`` SDK
    (``pip install 'dashdown-md[anthropic]'``)."""

    # Dashboard commentary is short and billed on every cache-miss, so the
    # default is the fast/cheap model; pin a more capable one (e.g.
    # claude-opus-4-8) via `llm.model` when answer quality matters more.
    DEFAULT_MODEL = "claude-haiku-4-5"

    # Commentary is a short paragraph or a few bullets; a small ceiling is
    # plenty and keeps the (non-streaming) call well under the SDK timeout
    # guard. `max_tokens` is required by the Messages API.
    MAX_TOKENS = 4096

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = None  # lazy: SDK import + client on first call

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as e:
                raise ImportError(
                    "LLM provider 'anthropic' requires the anthropic package. "
                    "Install it with: pip install 'dashdown-md[anthropic]'  "
                    f"(underlying error: {e})"
                ) from e
            self._client = anthropic.Anthropic(api_key=self.config.api_key)
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=self.config.model or self.DEFAULT_MODEL,
            max_tokens=self.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        # response.content is a list of content blocks; keep the text ones.
        return "".join(
            block.text for block in response.content if block.type == "text"
        )

    def stream_complete(self, system: str, prompt: str) -> Iterator[str]:
        client = self._get_client()
        with client.messages.stream(
            model=self.config.model or self.DEFAULT_MODEL,
            max_tokens=self.MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream


class OpenAIAdapter(LLMAdapter):
    """OpenAI chat completions via the official ``openai`` SDK
    (``pip install 'dashdown-md[openai]'``).

    Also the base for any OpenAI-compatible gateway (see ``OpenRouterAdapter``):
    a subclass only overrides ``BASE_URL`` / ``DEFAULT_MODEL`` / the install hint.
    """

    DEFAULT_MODEL: str | None = "gpt-4o-mini"
    #: ``None`` → the SDK default endpoint (api.openai.com).
    BASE_URL: str | None = None
    _PROVIDER = "openai"
    _EXTRA = "openai"

    def __init__(self, config: LLMConfig) -> None:
        super().__init__(config)
        self._client = None  # lazy: SDK import + client on first call

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as e:
                raise ImportError(
                    f"LLM provider '{self._PROVIDER}' requires the openai package. "
                    f"Install it with: pip install 'dashdown-md[{self._EXTRA}]'  "
                    f"(underlying error: {e})"
                ) from e
            kwargs: dict[str, Any] = {"api_key": self.config.api_key}
            if self.BASE_URL:
                kwargs["base_url"] = self.BASE_URL
            self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, system: str, prompt: str) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.config.model or self.DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""

    def stream_complete(self, system: str, prompt: str) -> Iterator[str]:
        client = self._get_client()
        stream = client.chat.completions.create(
            model=self.config.model or self.DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            stream=True,
        )
        for chunk in stream:
            # A usage/terminal chunk can carry an empty choices list.
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


class OpenRouterAdapter(OpenAIAdapter):
    """OpenRouter chat completions — the OpenAI SDK pointed at OpenRouter's
    OpenAI-compatible endpoint (``pip install 'dashdown-md[openrouter]'``).

    OpenRouter routes to many upstream models, so there's no sensible default:
    ``DEFAULT_MODEL = None`` makes ``llm.model`` required (validated at config
    load). Set it to a fully-qualified slug, e.g. ``anthropic/claude-3.5-sonnet``.
    """

    DEFAULT_MODEL: str | None = None
    BASE_URL = "https://openrouter.ai/api/v1"
    _PROVIDER = "openrouter"
    _EXTRA = "openrouter"


#: provider name -> adapter class. Future providers slot in here; tests
#: register fakes the same way.
_PROVIDERS: dict[str, type[LLMAdapter]] = {
    "mistral": MistralAdapter,
    "anthropic": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "openrouter": OpenRouterAdapter,
}


def known_providers() -> list[str]:
    return sorted(_PROVIDERS)


def unavailable_notice(config: LLMConfig) -> str:
    """Reader-facing message for an <Ask /> card when the LLM is off.

    One wording shared by the live endpoint and the static build, so a card
    says the same thing everywhere. A misconfigured block names the problem
    (it's the author who reads this while setting up); an absent block is the
    plain "not configured" case.
    """
    if config.error:
        return (
            "AI commentary is not available — the `llm:` block in "
            f"dashdown.yaml is misconfigured: {config.error}"
        )
    return (
        "AI commentary is not available — no LLM provider is configured "
        "(add an `llm:` block to dashdown.yaml)."
    )


def create_adapter(config: LLMConfig) -> LLMAdapter:
    if not config.enabled:
        raise ValueError("no LLM provider configured (add an `llm:` block to dashdown.yaml)")
    cls = _PROVIDERS.get(config.provider)
    if cls is None:
        raise ValueError(
            f"unknown llm.provider {config.provider!r} (expected one of {known_providers()})"
        )
    return cls(config)


def resolve_model_name(config: LLMConfig) -> str:
    """The model id that will actually be called for ``config`` — the
    ``llm.model`` override, else the provider adapter's ``DEFAULT_MODEL``.

    Derived from config alone (no SDK import / client construction), so the
    ``<Ask />`` endpoint and the static build can attach the model to every
    answer payload — including cache hits, before any adapter is touched.
    Returns ``""`` when no provider is configured.
    """
    cls = _PROVIDERS.get(config.provider)
    default = cls.DEFAULT_MODEL if cls is not None else None
    return config.model or default or ""


# --------------------------------------------------------------------------- #
# Ask-prompt registry (mirrors the query-def cache in render/pipeline.py)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AskDef:
    """One authored ``<Ask />`` block: a prompt pinned to a query, plus the
    block's own knobs (component attributes, not global config)."""

    id: str
    query_name: str
    connector: str
    prompt: str
    max_rows: int = DEFAULT_MAX_ROWS
    cache_ttl: int = DEFAULT_ANSWER_TTL
    # Whether the endpoint honors `_refresh=1` (the card's ↻ button). Off ⇒
    # viewers can't force fresh (billable) LLM calls — enforced server-side,
    # not just by hiding the button. Like cache_ttl, stays out of the id hash.
    allow_refresh: bool = True
    # Page frontmatter threaded into the prompt (see build_ask_prompt) so the
    # model knows what dashboard it's commenting on. Part of the id hash:
    # changed context ⇒ a different id ⇒ a fresh answer.
    page_title: str = ""
    page_description: str = ""


# Global, like _query_def_cache: the ask API is a separate request from the
# page render and must look the prompt back up.
_ask_def_cache: dict[str, AskDef] = {}


def ask_id(
    query_name: str,
    connector: str,
    prompt: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    page_title: str = "",
    page_description: str = "",
) -> str:
    """Deterministic id for an ask block — stable across renders/restarts.

    ``max_rows`` is part of the hash because it changes the data payload the
    model sees, so editing it must bust the answer cache; the page context is
    in because it changes the prompt (changed context ⇒ fresh answer);
    ``cache_ttl`` only affects expiry and stays out.
    """
    digest = hashlib.sha256(
        "\x00".join(
            (connector, query_name, prompt, str(max_rows), page_title, page_description)
        ).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def register_ask_def(
    query_name: str,
    connector: str,
    prompt: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    cache_ttl: int = DEFAULT_ANSWER_TTL,
    page_title: str = "",
    page_description: str = "",
    allow_refresh: bool = True,
) -> AskDef:
    d = AskDef(
        id=ask_id(query_name, connector, prompt, max_rows, page_title, page_description),
        query_name=query_name,
        connector=connector,
        prompt=prompt,
        max_rows=max_rows,
        cache_ttl=cache_ttl,
        page_title=page_title,
        page_description=page_description,
        allow_refresh=allow_refresh,
    )
    _ask_def_cache[d.id] = d
    return d


def get_ask_def(id: str) -> AskDef | None:
    return _ask_def_cache.get(id)


# --------------------------------------------------------------------------- #
# Answer cache: (ask id, relevant substituted params) -> (html, text, expiry)
#
# ``text`` is the model's raw Markdown answer, kept alongside the rendered
# ``html`` so a cache hit (and a static bake) can *replay* the answer with the
# same typewriter effect a live stream has — ask.js types the text out, then
# swaps in the sanitized html. Rendering always uses ``html``; the raw text is
# only ever shown as escaped plain text (textContent), exactly like the live
# SSE chunks, so shipping it widens nothing.
# --------------------------------------------------------------------------- #
_answer_cache: dict[tuple[str, tuple], tuple[str, str, float]] = {}


def relevant_params(sql: str, params: dict[str, str]) -> dict[str, str]:
    """Only the params the SQL actually substitutes (``${name}`` appears).

    Keying the answer cache on these — not on every filter the page happens to
    carry — keeps an unrelated filter change from triggering a fresh LLM call.
    """
    used = set(_PARAM_RE.findall(sql))
    return {k: v for k, v in params.items() if k in used}


def _freeze(params: dict[str, str]) -> tuple:
    return tuple(sorted(params.items()))


def get_cached_answer(ask_id: str, params: dict[str, str]) -> tuple[str, str] | None:
    """Return ``(html, raw answer text)`` for a live cache entry, else None."""
    key = (ask_id, _freeze(params))
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    html, text, expiry = entry
    if time.monotonic() > expiry:
        del _answer_cache[key]
        return None
    return html, text


def cache_answer(
    ask_id: str, params: dict[str, str], html: str, ttl: int, text: str = ""
) -> None:
    _answer_cache[(ask_id, _freeze(params))] = (html, text, time.monotonic() + ttl)


# --------------------------------------------------------------------------- #
# Commentary generation (shared by the live endpoint and `dashdown build`)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a data analyst writing short commentary for an analytics dashboard. "
    "You are given the result of a database query (column names, inferred types, "
    "and rows — possibly truncated) plus a question from the dashboard author. "
    "You may also be given the page's title and description, the active filters "
    "that produced the rows, and today's date — use them to ground your answer "
    "(e.g. name the filtered region, interpret 'recent' relative to today). "
    "Answer concisely in Markdown: a short paragraph or a few bullet points. "
    "Base every statement strictly on the data provided; if the question cannot "
    "be answered from the data, say so. Never invent numbers."
)


def _cell_text(v: Any) -> str:
    if v is None:
        return "NULL"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def format_result_for_llm(result: QueryResult, max_rows: int) -> str:
    """Cap the payload to column names + inferred types + the first N rows."""
    rows = result.rows[:max_rows]

    # Infer each column's type from its first non-null value.
    types: list[str] = []
    for i in range(len(result.columns)):
        t = "unknown"
        for row in rows:
            if i < len(row) and row[i] is not None:
                t = type(row[i]).__name__
                break
        types.append(t)

    lines = [
        "Columns: "
        + ", ".join(f"{c} ({t})" for c, t in zip(result.columns, types))
    ]
    total = len(result.rows)
    if total > len(rows):
        lines.append(f"Rows (first {len(rows)} of {total}; truncated):")
    else:
        lines.append(f"Rows ({total}):")
    lines.append(" | ".join(result.columns))
    for row in rows:
        lines.append(" | ".join(_cell_text(v) for v in row))
    return "\n".join(lines)


def build_ask_prompt(
    ask: AskDef, result: QueryResult, params: dict[str, str] | None = None
) -> str:
    """The user message for one ask: question + grounding context + capped data.

    The context block (page title/description, active filters, today's date)
    is what lets the model say "in the East region…" after a viewer filters,
    instead of commenting blind. ``params`` are the *substituted* values the
    query actually uses — the same set that keys the answer cache — so the
    prompt and the cache always agree.
    """
    lines = [f"Question: {ask.prompt}"]
    page = " — ".join(p for p in (ask.page_title, ask.page_description) if p)
    if page:
        lines.append(f"Dashboard page: {page}")
    lines.append(f"Today's date: {date.today().isoformat()}")
    active = {k: v for k, v in (params or {}).items() if v != ""}
    if active:
        lines.append("Active filters (already applied to the rows below):")
        lines.extend(f"- {k} = {v}" for k, v in sorted(active.items()))
    payload = format_result_for_llm(result, ask.max_rows)
    lines.append(f"\nResult of query '{ask.query_name}':\n{payload}")
    return "\n".join(lines)


def generate_answer(
    ask: AskDef,
    result: QueryResult,
    adapter: LLMAdapter,
    params: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Run one ask: build the prompt (capped to the ask's ``max_rows``), call
    the LLM, render the Markdown answer to HTML (raw HTML disabled, so model
    output can't inject markup into the page).

    Returns ``(html, raw answer text)`` — the raw text rides along in the
    answer cache / baked snapshot so the client can replay the answer as a
    typewriter (see the answer-cache comment above)."""
    answer = adapter.complete(SYSTEM_PROMPT, build_ask_prompt(ask, result, params))
    return render_markdown_text(answer), answer


def stream_answer(
    ask: AskDef,
    result: QueryResult,
    adapter: LLMAdapter,
    params: dict[str, str] | None = None,
) -> Iterator[str]:
    """Yield the model's raw answer text incrementally (the SSE slow path).

    The caller joins the chunks and renders them with ``render_markdown_text``
    — the same sanitized HTML :func:`generate_answer` produces — so
    streaming never widens what reaches the page.
    """
    yield from adapter.stream_complete(SYSTEM_PROMPT, build_ask_prompt(ask, result, params))
