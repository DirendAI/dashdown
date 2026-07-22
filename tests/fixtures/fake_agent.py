"""A scripted stand-in for a coding-agent CLI, driven by the edit-mode tests.

Invoked as ``python fake_agent.py <scenario>`` with the prompt on stdin (the
``stdin`` prompt_via path) and the project root as cwd — exactly how
edit_session.py runs a real agent. Scenarios:

- ``happy``: echo the prompt, edit ``pages/index.md``, create ``pages/new.md``,
  exit 0.
- ``fail``: write to stderr, exit 3.
- ``sleep``: print one line, then sleep ~60s (cancel/timeout tests kill it).
- ``noisy``: print many lines (ring-buffer cap test), exit 0.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path


def main() -> int:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "happy"
    prompt = sys.stdin.read()
    print("PROMPT:" + prompt.replace("\n", "\\n"), flush=True)

    if scenario == "fail":
        print("boom: something went wrong", file=sys.stderr, flush=True)
        return 3

    if scenario == "sleep":
        print("sleeping", flush=True)
        time.sleep(60)
        return 0

    if scenario == "noisy":
        for i in range(500):
            print(f"line {i}", flush=True)
        return 0

    # happy
    print("editing the page", flush=True)
    index = Path("pages") / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "\nEdited by the fake agent.\n",
        encoding="utf-8",
    )
    (Path("pages") / "new.md").write_text("# New page\n", encoding="utf-8")
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
