"""PostToolUse hook: auto-format and lint files edited by Edit/Write.

Fail-soft by design: if a formatter isn't installed yet (e.g. the frontend
hasn't been scaffolded), the hook exits quietly instead of blocking edits.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

WEB_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".md"}
LINT_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}


def run(cmd: list[str], cwd: Path | None = None) -> None:
    exe = shutil.which(cmd[0])
    if exe is None:
        return
    try:
        subprocess.run([exe, *cmd[1:]], cwd=cwd, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pass


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return
    path = Path(file_path)
    if not path.is_file():
        return
    root = Path(payload.get("cwd") or ".")
    suffix = path.suffix.lower()

    if suffix == ".py":
        run(["uv", "run", "ruff", "format", str(path)], cwd=root)
        run(["uv", "run", "ruff", "check", "--fix", str(path)], cwd=root)
    elif suffix in WEB_SUFFIXES:
        run(["pnpm", "exec", "prettier", "--write", str(path)], cwd=root)
        if suffix in LINT_SUFFIXES:
            run(["pnpm", "exec", "eslint", "--fix", str(path)], cwd=root)
    elif suffix == ".rs":
        run(["rustfmt", str(path)])


if __name__ == "__main__":
    main()
