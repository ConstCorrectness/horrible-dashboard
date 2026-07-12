"""Loadout versioning: v1 files upgrade in place, branching/activating/deleting
versions, and match-log attribution stats."""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.games import loadout as L
from backend.modules.games import match_log


def _write_v1(path: Path, game_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({game_id: {"context": "old context", "tools": [], "model": None}}),
        encoding="utf-8",
    )


def test_v1_file_reads_as_active_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    _write_v1(tmp_path / "games_loadouts.json", "tictactoe")
    loadout = L.get_loadout("tictactoe")
    assert loadout.context == "old context"
    assert L.active_version_id("tictactoe") == "v1"
    assert [v["id"] for v in L.list_versions("tictactoe")] == ["v1"]


def test_save_version_branches_and_activates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    L.save_loadout(L.Loadout(game_id="tictactoe", context="first"))
    vid = L.save_version(
        "tictactoe", L.Loadout(game_id="tictactoe", context="second"), "corner opener"
    )
    assert vid == "v2"
    assert L.get_loadout("tictactoe").context == "second"
    versions = L.list_versions("tictactoe")
    assert {v["id"] for v in versions} == {"v1", "v2"}
    assert next(v for v in versions if v["id"] == "v2")["label"] == "corner opener"
    assert next(v for v in versions if v["id"] == "v2")["active"]

    # Flip back to v1 — the active version is what plays.
    assert L.activate_version("tictactoe", "v1")
    assert L.get_loadout("tictactoe").context == "first"


def test_delete_version_guards(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    L.save_loadout(L.Loadout(game_id="t", context="only"))
    assert not L.delete_version("t", "v1")  # never the last one
    L.save_version("t", L.Loadout(game_id="t", context="two"), "")
    assert L.delete_version("t", "v2")  # deleting the active one…
    assert L.active_version_id("t") == "v1"  # …falls back to the newest remaining


def test_save_loadout_keeps_model_and_updates_active(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    model = {"provider": "ollama", "model": "llama3"}
    L.save_loadout(L.Loadout(game_id="t", context="x", model=model))
    assert L.get_loadout("t").model == model


def test_match_log_attribution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    match_log.append_entry(
        game_id="tictactoe",
        table_id="t1",
        seat=0,
        winner=0,
        loadout_version="v2",
        model_label="ollama/llama3 (local)",
    )
    match_log.append_entry(
        game_id="tictactoe",
        table_id="t2",
        seat=1,
        winner=0,
        loadout_version="v2",
        model_label="ollama/llama3 (local)",
    )
    match_log.attach_rating("tictactoe", 12.5, 1212.5, "placement")
    stats = match_log.version_stats("tictactoe")
    assert stats["v2"] == {"win": 1, "loss": 1, "draw": 0}
    entries = match_log.list_entries("tictactoe")
    assert entries[0]["result"] == "loss"  # newest first
    # attach_rating stamps the newest un-stamped entry.
    assert any(e["rating_delta"] == 12.5 for e in entries)
