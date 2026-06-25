<!-- AUTO-GENERATED from docs/pages/ by tooling/gen-agent-docs.py — do not edit. -->
<!-- Topic: embedding. Regenerate with: python tooling/gen-agent-docs.py -->

<!-- source: docs/pages/embedding.md -->

# Embedding

Any page can be served chrome-less for embedding in another site via an
auto-resizing iframe. Framing is **deny-by-default**: you opt in per project.

```yaml
# dashdown.yaml
embed:
  enabled: true
  frame_ancestors:
    - https://your-site.example
  # secret: ${EMBED_SECRET}   # required when auth is on (signed, page-scoped tokens)
  # token_ttl: 3600
```

Add `?_embed` to a page URL to render it without the header/sidebar/breadcrumbs.
The host page includes the small `embed.js` loader, which drops an
auto-resizing iframe and listens for the posted content height.

## Authenticated embeds

When the project has an `auth:` block, a cross-origin iframe can't send
credentials — so an authed page is embedded with a signed, page-scoped token
(`?_embed=<token>`), minted by `GET /_dashdown/api/embed-token?path=…` or the
`dashdown embed-token` CLI. The token is an HMAC scoped to that exact page and the
queries it reads, so it can't be replayed for other resources.

:::tip
The **Embed** button (on the breadcrumb line, top-right of every page) copies a ready-made snippet for the current page.
:::
