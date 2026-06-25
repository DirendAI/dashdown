# Licensing — the plain-English version

Dashdown is **free and open source** under the **GNU AGPL-3.0-or-later** (see [LICENSE](./LICENSE)
for the full legal text). This page explains, in everyday terms, what you can do for free and when
you'd need to talk to us about a commercial license. It's a friendly summary, **not** the legal
contract — where the two differ, [LICENSE](./LICENSE) wins.

## The one rule to remember

AGPL only ever asks for one thing:

> **If people use *your* version of Dashdown, you have to let those same people get the source code.**

It never asks for money, and it never forces you to publish your code to the public internet. The
obligation only runs to the people who actually use your instance.

## What you can do for free

✅ **Use it, commercially, forever** — at work, for profit, no payment, no permission needed.

✅ **Run it for your team** — install it, host it internally for your employees. Nothing required
beyond keeping the license notice.

✅ **Modify it for internal use** — tweak it, fix bugs, build dashboards. The only obligation is that
the people using your instance (e.g. your own staff) can get the code. In practice that's "put a link
to the source on your intranet." **Nothing goes public.**

✅ **Write custom components and connectors** for your own deployment — same deal: your own users must
be able to get the code, but you never have to publish it to the world.

## When you need a **commercial license** instead

You can't keep your changes private *and* hand Dashdown (or a product built on it) to outside parties.
Specifically, the AGPL would otherwise require you to open-source your code if you:

❌ **Embed Dashdown inside a closed-source product** you ship or sell to customers.

❌ **Offer a hosted/SaaS product built on a modified Dashdown** to the public.

❌ **Distribute a proprietary (closed-source) component or connector** that plugs into Dashdown.

In those cases you have two options: open-source your own work under the AGPL too, **or** buy a
commercial license from us that lifts the AGPL obligations.

> **Why custom plugins count:** a component or connector that plugs into Dashdown (subclassing its
> classes, running in the same process) is treated as *part of* Dashdown. Internal use is fine; but you
> can't ship it as a closed-source add-on without a commercial license. The way to keep something fully
> separate and proprietary is to run it as its **own service** that talks to Dashdown over the network,
> rather than as an in-process plugin.

## Open core: the free framework vs. the paid add-ons

- **Dashdown core** (this repository) is AGPL — free, open, yours to use and extend.
- **Paid enterprise modules** (e.g. SSO, role-based access, multi-tenant row isolation, audit console)
  are a **separate, commercially-licensed** product. They are not part of this AGPL repository.

Because we hold the copyright to the core, we can offer it under AGPL to everyone *and* sell commercial
licenses + proprietary modules. That's the whole model: **the community gets a real open-source tool,
and a competitor can't turn it into a closed product without paying.**

## Want a commercial license?

If AGPL doesn't fit your situation — your company bans AGPL, you want to embed Dashdown in a
closed-source product, or you want to ship proprietary plugins — get in touch:

**Contact:** info@dirend.ai

---

*Summary only — not legal advice. The binding terms are in [LICENSE](./LICENSE).*
