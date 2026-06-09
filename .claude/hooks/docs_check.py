"""Stop hook: nudge to keep docs/ in sync with code changes.

If the working tree has changes under the code roots (apps/, packages/,
backend/) but nothing under docs/, block the stop once with a reminder to
update the docs or state why they are unaffected. A state file remembers the
last reviewed change set so the same nudge doesn't repeat every turn.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

CODE_PREFIXES = ("apps/", "packages/", "backend/")
STATE_FILE = Path(".claude/.docs_check_state")


def changed_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    files = []
    for line in out.splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            files.append(path)
    return files


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if payload.get("stop_hook_active"):
        return

    files = changed_files()
    code = sorted(f for f in files if f.startswith(CODE_PREFIXES))
    docs_touched = any(f.startswith("docs/") for f in files)
    if not code or docs_touched:
        return

    digest = hashlib.sha256("\n".join(code).encode()).hexdigest()
    if STATE_FILE.exists() and STATE_FILE.read_text().strip() == digest:
        return
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(digest)

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    "Docs sync check: this change set touches "
                    + ", ".join(code[:10])
                    + (" …" if len(code) > 10 else "")
                    + " but nothing under docs/. Per docs/README.md, new or "
                    "changed modules, panels, commands, capabilities, or "
                    "layout-shell behavior must update the matching page in "
                    "docs/. Update the relevant docs now, or if these changes "
                    "genuinely don't affect anything documented (pure refactor, "
                    "config, tests), state that briefly and finish."
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
