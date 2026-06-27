"""Scaffold (`dashdown new`) drops a working project plus the coding-agent guide.

The guide is progressive-disclosure: a slim `AGENTS.md` *map* (cheat-sheet + a table of
contents) plus per-topic `references/<topic>.md` shards, routed by the Claude skill.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from dashdown.cli import _install_agent_docs, _resolve_targets, _scaffold, app

REPO_ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()


def _gen_in_subprocess(fn: str) -> dict[str, str]:
    """Run a ``gen-agent-docs.py`` builder in a clean subprocess (see `_fresh_outputs`)."""
    path = REPO_ROOT / "tooling" / "gen-agent-docs.py"
    code = textwrap.dedent(
        f"""
        import importlib.util, json, sys
        spec = importlib.util.spec_from_file_location("gen_agent_docs", r"{path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        json.dump(mod.{fn}(), sys.stdout)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _fresh_outputs() -> dict[str, str]:
    """Run the generator's ``build_outputs()`` in a **clean subprocess**.

    ``references/catalog.md`` is introspected from the live component registry, and
    other tests (``test_build``/``test_project``) register fixture components
    (``Widget``/``Flat``) into that global registry — so generating in-process would
    pick up that pollution and spuriously "drift" from the committed catalog. A fresh
    process sees only the built-ins, exactly as ``python tooling/gen-agent-docs.py`` does
    at release time.
    """
    return _gen_in_subprocess("build_outputs")


def test_scaffold_writes_core_project(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    assert (tmp_path / "dashdown.yaml").is_file()
    assert (tmp_path / "sources.yaml").is_file()
    assert (tmp_path / "pages" / "index.md").is_file()
    assert (tmp_path / "data" / "sales.csv").is_file()


def test_scaffold_includes_agents_map(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()
    text = agents.read_text(encoding="utf-8")
    assert "Dashdown" in text
    # It's the slim *map*, not the old monolith: a cheat-sheet + an index into the shards.
    assert "Cheat-sheet" in text
    assert "## Reference index" in text
    assert "references/components.md" in text  # the TOC links into the shards
    # Live `:::query` data blocks are stripped; only fenced examples survive.
    assert ":::query" not in text or "```" in text


def test_scaffold_includes_reference_shards(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    refs = tmp_path / "references"
    assert refs.is_dir()
    # Core topics each get a shard (one per top-level entry in docs/pages/).
    for slug in ("components", "connectors", "queries", "semantic-layer", "cli", "filters"):
        assert (refs / f"{slug}.md").is_file(), slug
    # The detail lives in the shards now, not the map.
    assert (refs / "components.md").stat().st_size > 5000
    # Every reference link in the map resolves to an emitted shard.
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    hrefs = re.findall(r"\]\((references/[\w.\-]+\.md)\)", agents)
    assert hrefs, "the map should link to reference shards"
    for href in hrefs:
        assert (tmp_path / href).is_file(), href


def test_scaffold_includes_registry_catalog(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    catalog = tmp_path / "references" / "catalog.md"
    assert catalog.is_file()
    text = catalog.read_text(encoding="utf-8")
    # Registry-introspected (not a docs/ page) — the same data `dashdown components` prints.
    assert "dashdown components" in text
    assert "## Components" in text and "## Connectors" in text
    # A known component with its attrs, and a known connector with its config keys.
    assert "`BarChart`" in text and "`series`" in text
    assert "`postgres`" in text and "`host`" in text
    # The map links to it.
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "references/catalog.md" in agents


def test_scaffold_includes_skill_router(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    skill = tmp_path / ".claude" / "skills" / "dashdown-authoring" / "SKILL.md"
    assert skill.is_file()
    body = skill.read_text(encoding="utf-8")
    assert "name: dashdown-authoring" in body
    # The skill routes to the map and into the reference shards; the relative links must resolve.
    assert "../../../AGENTS.md" in body
    assert (skill.parent / "../../../AGENTS.md").resolve() == (tmp_path / "AGENTS.md").resolve()
    assert "../../../references/" in body
    assert (skill.parent / "../../../references/components.md").resolve() == (
        tmp_path / "references" / "components.md"
    ).resolve()


def test_agent_docs_are_freshly_generated() -> None:
    """Committed scaffold artifacts must match a fresh run of the generator.

    Locks the CLAUDE.md discipline — *re-run `gen-agent-docs.py` after editing `docs/`* —
    so the shipped guide can't silently drift from the docs (same failure mode as a stale
    Tailwind bundle). Skipped where the `docs/` source isn't present (a packaged install).
    """
    if not (REPO_ROOT / "docs" / "pages").is_dir():
        pytest.skip("docs/ project not present (packaged install)")
    outputs = _fresh_outputs()
    scaffold = REPO_ROOT / "dashdown" / "scaffold"
    for rel, content in outputs.items():
        committed = scaffold / rel
        assert committed.is_file(), f"missing {rel} — run: python tooling/gen-agent-docs.py"
        assert committed.read_text(encoding="utf-8") == content, (
            f"{rel} is stale — run: python tooling/gen-agent-docs.py"
        )
    # No ghost shard committed that a fresh generation wouldn't emit.
    for shard in (scaffold / "references").glob("*.md"):
        rel = f"references/{shard.name}"
        assert rel in outputs, f"stale shard {rel} — run: python tooling/gen-agent-docs.py"


# --- `dashdown skill` (install/update the guide in an existing project) ------------------


def test_skill_installs_guide_into_existing_project(tmp_path: Path) -> None:
    (tmp_path / "dashdown.yaml").write_text("title: X\n", encoding="utf-8")
    written, skipped = _install_agent_docs(tmp_path, refresh=False)

    assert "AGENTS.md" in written
    assert any(w.startswith("references/") for w in written)
    assert any(".claude/skills/dashdown-authoring/SKILL.md" in w for w in written)
    assert skipped == []
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / "references" / "components.md").is_file()
    assert (tmp_path / ".claude" / "skills" / "dashdown-authoring" / "SKILL.md").is_file()


def test_skill_is_idempotent_without_refresh(tmp_path: Path) -> None:
    _install_agent_docs(tmp_path, refresh=False)
    # A local edit must survive a second non-refresh install (it only fills gaps).
    agents = tmp_path / "AGENTS.md"
    agents.write_text("MY EDITS\n", encoding="utf-8")
    written, skipped = _install_agent_docs(tmp_path, refresh=False)

    assert written == []  # nothing new to write
    assert "AGENTS.md" in skipped
    assert agents.read_text(encoding="utf-8") == "MY EDITS\n"  # preserved


def test_skill_refresh_overwrites_and_prunes_ghosts(tmp_path: Path) -> None:
    _install_agent_docs(tmp_path, refresh=False)
    (tmp_path / "AGENTS.md").write_text("STALE\n", encoding="utf-8")
    ghost = tmp_path / "references" / "old-removed-topic.md"
    ghost.write_text("ghost\n", encoding="utf-8")

    written, _ = _install_agent_docs(tmp_path, refresh=True)

    assert "AGENTS.md" in written
    assert "Dashdown" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")  # restored
    assert not ghost.exists()  # stale shard pruned, like the generator's evict


def test_skill_command_runs(tmp_path: Path) -> None:
    (tmp_path / "dashdown.yaml").write_text("title: X\n", encoding="utf-8")
    res = runner.invoke(app, ["skill", "-p", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "AGENTS.md").is_file()
    # Second run reports nothing new (idempotent) and stays exit 0.
    res2 = runner.invoke(app, ["skill", "-p", str(tmp_path)])
    assert res2.exit_code == 0
    assert "up to date" in res2.output.lower() or "Kept" in res2.output


# --- Multi-tool targets (--target / dashdown.yaml `agents:`) ------------------------------


def test_scaffold_seeds_agents_default_claude(tmp_path: Path) -> None:
    # `dashdown new` with no flag records its default so a later `dashdown skill` honors it.
    _scaffold(tmp_path)
    cfg = (tmp_path / "dashdown.yaml").read_text(encoding="utf-8")
    assert "agents: [claude]" in cfg


def test_baseline_always_installed_regardless_of_target(tmp_path: Path) -> None:
    # The tool-agnostic content ships for every target; only the wrapper varies.
    written, _ = _install_agent_docs(tmp_path, refresh=False, targets=["cursor"])
    assert "AGENTS.md" in written
    assert any(w.startswith("references/") for w in written)
    assert (tmp_path / "references" / "components.md").is_file()


def test_explicit_target_installs_only_that_wrapper(tmp_path: Path) -> None:
    (tmp_path / "dashdown.yaml").write_text("title: X\n", encoding="utf-8")
    _install_agent_docs(tmp_path, refresh=False, targets=["cursor"])
    rule = tmp_path / ".cursor" / "rules" / "dashdown.mdc"
    assert rule.is_file()
    assert not (tmp_path / ".claude").exists()  # claude not requested
    body = rule.read_text(encoding="utf-8")
    assert "alwaysApply: true" in body  # a real Cursor rule …
    assert "AGENTS.md" in body  # … that routes into the shared guide


def test_mistral_mirrors_claude_skill_layout(tmp_path: Path) -> None:
    # `.vibe/` uses the same `skills/<name>/SKILL.md` layout as `.claude/`, so it ships the
    # identical skill — including the `../../../AGENTS.md` links, which resolve to the project
    # root from either location (same depth).
    _install_agent_docs(tmp_path, refresh=False, targets=["claude", "mistral"])
    claude_skill = tmp_path / ".claude" / "skills" / "dashdown-authoring" / "SKILL.md"
    vibe_skill = tmp_path / ".vibe" / "skills" / "dashdown-authoring" / "SKILL.md"
    assert claude_skill.is_file() and vibe_skill.is_file()
    assert vibe_skill.read_text(encoding="utf-8") == claude_skill.read_text(encoding="utf-8")
    assert (vibe_skill.parent / "../../../AGENTS.md").resolve() == (tmp_path / "AGENTS.md").resolve()


def test_target_resolution_precedence(tmp_path: Path) -> None:
    cfg = tmp_path / "dashdown.yaml"
    # Fallback when there's nothing to go on.
    assert _resolve_targets(tmp_path, None) == ["claude"]
    # dashdown.yaml `agents:` drives it …
    cfg.write_text("title: X\nagents: [cursor, gemini]\n", encoding="utf-8")
    assert _resolve_targets(tmp_path, None) == ["cursor", "gemini"]
    # … an explicit --target overrides the config …
    assert _resolve_targets(tmp_path, ["gemini"]) == ["gemini"]
    # … and with neither, an existing marker dir is auto-detected.
    cfg.write_text("title: X\n", encoding="utf-8")
    (tmp_path / ".cursor").mkdir()
    assert _resolve_targets(tmp_path, None) == ["cursor"]


def test_unknown_target_errors(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter):
        _resolve_targets(tmp_path, ["bogus"])


def test_new_target_installs_multiple_and_seeds_yaml(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    res = runner.invoke(app, ["new", str(proj), "--target", "claude,cursor"])
    assert res.exit_code == 0, res.output
    assert (proj / ".claude" / "skills" / "dashdown-authoring" / "SKILL.md").is_file()
    assert (proj / ".cursor" / "rules" / "dashdown.mdc").is_file()
    assert (proj / "AGENTS.md").is_file()  # baseline always present
    # The choice is recorded so `dashdown skill` later keeps both in sync.
    assert "agents: [claude, cursor]" in (proj / "dashdown.yaml").read_text(encoding="utf-8")


def test_skill_command_honors_yaml_agents(tmp_path: Path) -> None:
    (tmp_path / "dashdown.yaml").write_text("title: X\nagents: [cursor]\n", encoding="utf-8")
    res = runner.invoke(app, ["skill", "-p", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert (tmp_path / ".cursor" / "rules" / "dashdown.mdc").is_file()
    assert not (tmp_path / ".claude").exists()


# --- llms.txt / llms-full.txt (published on the docs static build) -----------------------


def _fresh_llms_outputs() -> dict[str, str]:
    return _gen_in_subprocess("build_llms_outputs")


@pytest.mark.skipif(
    not (REPO_ROOT / "docs" / "pages").is_dir(), reason="docs/ project not present"
)
def test_llms_outputs_shape() -> None:
    outputs = _fresh_llms_outputs()
    assert set(outputs) == {"llms.txt", "llms-full.txt"}
    # The map is small + links into the docs; the monolith is the whole manual.
    assert "## Documentation" in outputs["llms.txt"]
    assert "/llms-full.txt" in outputs["llms.txt"]
    assert "Component & connector catalog" in outputs["llms-full.txt"]
    assert len(outputs["llms-full.txt"]) > 10 * len(outputs["llms.txt"])


@pytest.mark.skipif(
    not (REPO_ROOT / "docs" / "pages").is_dir(), reason="docs/ project not present"
)
def test_llms_txt_links_resolve_to_docs_pages() -> None:
    outputs = _fresh_llms_outputs()
    pages = REPO_ROOT / "docs" / "pages"
    links = re.findall(r"\]\((/[\w.\-]+)\)", outputs["llms.txt"])
    assert links, "llms.txt should link to topic pages"
    for link in links:
        if link == "/llms-full.txt":
            continue  # a sibling output, not a page
        slug = link.lstrip("/")
        assert (pages / f"{slug}.md").is_file() or (
            pages / slug / "index.md"
        ).is_file(), f"{link} resolves to no docs page"


@pytest.mark.skipif(
    not (REPO_ROOT / "docs" / "pages").is_dir(), reason="docs/ project not present"
)
def test_llms_txt_is_freshly_generated() -> None:
    """Committed docs/llms.txt + llms-full.txt must match a fresh generation."""
    outputs = _fresh_llms_outputs()
    docs = REPO_ROOT / "docs"
    for rel, content in outputs.items():
        committed = docs / rel
        assert committed.is_file(), f"missing docs/{rel} — run: python tooling/gen-agent-docs.py"
        assert committed.read_text(encoding="utf-8") == content, (
            f"docs/{rel} is stale — run: python tooling/gen-agent-docs.py"
        )
