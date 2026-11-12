"""Test setup for the karaoke module, self-contained on purpose.

A module's tests must run from wherever the module lives: in this repo, or checked
out on its own as a git submodule. So this file does not lean on
`backend/tests/conftest.py`. It puts `py/src` on the path (the host does the same at
load time, see `backend/sdk/module_scan.py`) and points every node root at a tmp
directory so no test touches the developer's real `~/.horrible`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def isolate_node_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRETS_KEY_PATH", str(tmp_path / "secrets.key"))
    monkeypatch.delenv("SECRETS_MASTER_KEY", raising=False)
    # Siblings of tmp_path, never children (see backend/paths.py `config_dir`).
    monkeypatch.setenv(
        "HORRIBLE_CONFIG_DIR", str(tmp_path.with_name(f"{tmp_path.name}-config"))
    )
    monkeypatch.setenv(
        "HORRIBLE_CACHE_DIR", str(tmp_path.with_name(f"{tmp_path.name}-cache"))
    )
