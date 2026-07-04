# Unpublished docs drafts

Pages held back from the served docs (and from the generated agent guide —
`tooling/gen-agent-docs.py` shards only `docs/pages/`).

- `authentication.md` / `embedding.md` — enterprise features, postponed until
  they're ready. The implementation stays in-tree (`dashdown/auth.py`,
  `dashdown/embed.py`) behind the gate in `dashdown/enterprise.py`
  (`DASHDOWN_ENTERPRISE=1` unlocks). Move a page back into `pages/` and re-run
  the agent-docs generator to republish it.
