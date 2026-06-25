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
then report that no provider is configured instead of failing the page.
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
from dataclasses import dataclass
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
    """

    provider: str = "none"
    model: str | None = None
    api_key: str = ""

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

    Raises ``ValueError`` on misconfiguration so the server refuses to start
    half-configured (same policy as ``auth:`` / ``branding:``).
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


def create_adapter(config: LLMConfig) -> LLMAdapter:
    if not config.enabled:
        raise ValueError("no LLM provider configured (add an `llm:` block to dashdown.yaml)")
    cls = _PROVIDERS.get(config.provider)
    if cls is None:
        raise ValueError(
            f"unknown llm.provider {config.provider!r} (expected one of {known_providers()})"
        )
    return cls(config)


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


# Global, like _query_def_cache: the ask API is a separate request from the
# page render and must look the prompt back up.
_ask_def_cache: dict[str, AskDef] = {}


def ask_id(
    query_name: str, connector: str, prompt: str, max_rows: int = DEFAULT_MAX_ROWS
) -> str:
    """Deterministic id for an ask block — stable across renders/restarts.

    ``max_rows`` is part of the hash because it changes the data payload the
    model sees, so editing it must bust the answer cache; ``cache_ttl`` only
    affects expiry and stays out.
    """
    digest = hashlib.sha256(
        f"{connector}\x00{query_name}\x00{prompt}\x00{max_rows}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def register_ask_def(
    query_name: str,
    connector: str,
    prompt: str,
    max_rows: int = DEFAULT_MAX_ROWS,
    cache_ttl: int = DEFAULT_ANSWER_TTL,
) -> AskDef:
    d = AskDef(
        id=ask_id(query_name, connector, prompt, max_rows),
        query_name=query_name,
        connector=connector,
        prompt=prompt,
        max_rows=max_rows,
        cache_ttl=cache_ttl,
    )
    _ask_def_cache[d.id] = d
    return d


def get_ask_def(id: str) -> AskDef | None:
    return _ask_def_cache.get(id)


# --------------------------------------------------------------------------- #
# Answer cache: (ask id, relevant substituted params) -> (html, expiry)
# --------------------------------------------------------------------------- #
_answer_cache: dict[tuple[str, tuple], tuple[str, float]] = {}


def relevant_params(sql: str, params: dict[str, str]) -> dict[str, str]:
    """Only the params the SQL actually substitutes (``${name}`` appears).

    Keying the answer cache on these — not on every filter the page happens to
    carry — keeps an unrelated filter change from triggering a fresh LLM call.
    """
    used = set(_PARAM_RE.findall(sql))
    return {k: v for k, v in params.items() if k in used}


def _freeze(params: dict[str, str]) -> tuple:
    return tuple(sorted(params.items()))


def get_cached_answer(ask_id: str, params: dict[str, str]) -> str | None:
    key = (ask_id, _freeze(params))
    entry = _answer_cache.get(key)
    if entry is None:
        return None
    html, expiry = entry
    if time.monotonic() > expiry:
        del _answer_cache[key]
        return None
    return html


def cache_answer(ask_id: str, params: dict[str, str], html: str, ttl: int) -> None:
    _answer_cache[(ask_id, _freeze(params))] = (html, time.monotonic() + ttl)


# --------------------------------------------------------------------------- #
# Commentary generation (shared by the live endpoint and `dashdown build`)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are a data analyst writing short commentary for an analytics dashboard. "
    "You are given the result of a database query (column names, inferred types, "
    "and rows — possibly truncated) plus a question from the dashboard author. "
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


def generate_answer_html(
    ask: AskDef, result: QueryResult, adapter: LLMAdapter
) -> str:
    """Run one ask: build the prompt (capped to the ask's ``max_rows``), call
    the LLM, render the Markdown answer to HTML (raw HTML disabled, so model
    output can't inject markup into the page)."""
    payload = format_result_for_llm(result, ask.max_rows)
    user_prompt = (
        f"Question: {ask.prompt}\n\n"
        f"Result of query '{ask.query_name}':\n{payload}"
    )
    answer = adapter.complete(SYSTEM_PROMPT, user_prompt)
    return render_markdown_text(answer)
