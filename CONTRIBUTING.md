# Contributing to Dashdown

Thanks for your interest in contributing! This document covers how to work on Dashdown and — most
importantly — the licensing terms your contributions are made under. **Please read the
[Licensing of contributions](#licensing-of-contributions) section before opening a pull request.**

## Development setup

```bash
pip install -e .          # editable install (or: uv sync)
uv run pytest tests/ -v   # run the full suite (~90 tests) — must pass before a PR
```

Verify rendering changes by hand against the bundled `docs/` project, which is a real Dashdown
project (it documents the framework) and doubles as an integration fixture:

```bash
dashdown serve docs   # http://127.0.0.1:8000
```

A few conventions (see [CLAUDE.md](./CLAUDE.md) for the full picture):

- Python targets 3.10+; every module starts with `from __future__ import annotations` and uses
  modern type hints (`X | None`).
- There's no JS bundler or frontend build — static ES modules are shipped as-is.
- Add tests for any change to the render pipeline, SQL parameter substitution, or a connector.
- Keep new code in the style of the code around it.

## Licensing of contributions

Dashdown is **open core**: the framework is licensed under the AGPL-3.0-or-later, and a commercial
dual-license is offered on top (see [LICENSING.md](./LICENSING.md)). For that model to work, every
contribution to the core must be usable under **both** licenses. So, **by submitting a contribution**
(a pull request, patch, or any code/content) to this project, you agree to the following:

1. **Provenance (DCO).** You certify the [Developer Certificate of Origin](#developer-certificate-of-origin)
   below — i.e. you wrote the contribution, or otherwise have the right to submit it under these terms.

2. **Dual-license grant.** You license your contribution to the project under the AGPL-3.0-or-later,
   **and** you grant the project's maintainers a perpetual, worldwide, non-exclusive, royalty-free,
   irrevocable license to use, reproduce, modify, sublicense, and distribute your contribution —
   including the right to **relicense it under other terms, such as a commercial/proprietary license.**

   This is what allows Dashdown to be offered both as free AGPL software and under the commercial
   license described in [LICENSING.md](./LICENSING.md) **without re-contacting every contributor.** You
   retain copyright to your contribution; this is a license grant, not an assignment.

### Signing off (DCO)

Add a `Signed-off-by` line to every commit by committing with `-s`:

```bash
git commit -s -m "Your message"
```

This appends a line using your real name and email:

```
Signed-off-by: Jane Developer <jane@example.com>
```

That line is your certification of the DCO below.

### Developer Certificate of Origin

```
Developer Certificate of Origin
Version 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same open source license (unless I am
    permitted to submit under a different license), as indicated
    in the file; or

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

---

> **Note for maintainers:** the dual-license grant above is a lightweight, in-repo inbound license. It
> is a reasonable starting point, but the more robust form for a commercial open-core project is a
> **signed CLA** (e.g. via a CLA-assistant bot). Have a lawyer review this grant — and consider
> upgrading to a signed CLA — before accepting substantial outside contributions.
