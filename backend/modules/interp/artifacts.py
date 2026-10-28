"""Downloaded interpretability artifacts: where they are, what they are, what they fit.

One table in `app.db` (`interp_artifacts`), one row per artifact — an SAE folder, a
transcoder set, a lens — with the facts the compatibility check needs: the model it
was trained on, its input width, its layer. Files live under `$DATA/interp/artifacts/
<repo>/…`, keeping the Hub's own paths, so an artifact on disk reads like its repo.

Downloads follow the llama.cpp catalog's rules: the size is known before a byte is
written (the caller has shown it and the user has confirmed), the budget is checked
against it up front, and every file is written to `.part` and renamed on success —
a truncated `params.safetensors` would otherwise load and produce garbage features
instead of failing.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

import httpx

from backend import paths
from backend.modules.database.app_db import ensure_app_db_dir
from backend.modules.interp import catalog

logger = logging.getLogger(__name__)

DEFAULT_BUDGET_GB = 40


def artifacts_root() -> Path:
    return paths.data_dir() / "interp" / "artifacts"


def budget_bytes() -> int:
    from backend.modules.settings.routes import get_value

    try:
        gb = float(get_value("interp.libraryBudgetGb", DEFAULT_BUDGET_GB))
    except (TypeError, ValueError):
        gb = DEFAULT_BUDGET_GB
    return int(gb * 1024**3) if gb > 0 else 0


# ── the table ────────────────────────────────────────────────────────────────


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(ensure_app_db_dir()))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS interp_artifacts (
                id TEXT PRIMARY KEY,
                entry_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                repo TEXT NOT NULL,
                choice TEXT NOT NULL,
                model_repo TEXT,
                path TEXT NOT NULL,
                bytes INTEGER NOT NULL,
                facts TEXT NOT NULL,
                created_at REAL NOT NULL
            )"""
        )
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "entryId": r["entry_id"],
        "kind": r["kind"],
        "repo": r["repo"],
        "choice": r["choice"],
        "modelRepo": r["model_repo"],
        "path": r["path"],
        "bytes": r["bytes"],
        "facts": json.loads(r["facts"] or "{}"),
        "createdAt": r["created_at"],
    }


def list_artifacts() -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM interp_artifacts ORDER BY created_at DESC"
        ).fetchall()
    return [_row(r) for r in rows]


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    with _db() as conn:
        r = conn.execute(
            "SELECT * FROM interp_artifacts WHERE id = ?", (artifact_id,)
        ).fetchone()
    return _row(r) if r else None


def used_bytes() -> int:
    with _db() as conn:
        r = conn.execute(
            "SELECT COALESCE(SUM(bytes), 0) AS n FROM interp_artifacts"
        ).fetchone()
    return int(r["n"])


def delete_artifact(artifact_id: str) -> bool:
    found = get_artifact(artifact_id)
    if found is None:
        return False
    target = Path(found["path"])
    root = artifacts_root().resolve()
    # Only ever inside our own root: a row pointing elsewhere is not ours to delete.
    if target.resolve().is_relative_to(root) and target.exists():
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    with _db() as conn:
        conn.execute("DELETE FROM interp_artifacts WHERE id = ?", (artifact_id,))
    return True


def artifact_id(entry_id: str, choice: str) -> str:
    return f"{entry_id}:{choice or 'all'}"


# ── facts ────────────────────────────────────────────────────────────────────

_WIDTH = re.compile(r"width_(\d+)([km]?)", re.I)
_LAYER = re.compile(r"layer_(\d+)", re.I)

#: Config keys that state the input width / dictionary width / layer, across the
#: conventions in the wild (SAELens, Gemma Scope, circuit-tracer).
_DIN_KEYS = ("d_in", "d_model", "hidden_size", "input_dim", "d_input")
_WIDTH_KEYS = ("d_sae", "width", "dict_size", "d_transcoder", "n_features")
_LAYER_KEYS = ("hook_layer", "layer", "layer_idx")


def _parse_width(number: str, unit: str) -> int:
    scale = {"k": 1024, "m": 1024**2}.get(unit.lower(), 1)
    return int(number) * scale


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def facts_for(choice: str, files: dict[str, Path]) -> dict[str, Any]:
    """What an artifact states about itself: input width, dictionary width, layer.

    Read from its config file when it has one, else from Gemma Scope's folder naming
    (`layer_13_width_16k_l0_medium`). Each fact records where it came from, because
    a width parsed from a folder name is a weaker claim than one read from a config.
    """
    facts: dict[str, Any] = {"source": {}}
    for name, path in files.items():
        if not name.endswith((".json", ".yaml", ".yml")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            if name.endswith(".json"):
                data = json.loads(text)
            else:
                import yaml

                data = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001 — an unreadable config is a missing fact
            logger.info("interp: could not read %s (%s)", path, exc)
            continue
        if not isinstance(data, dict):
            continue
        for fact, keys in (
            ("dIn", _DIN_KEYS),
            ("width", _WIDTH_KEYS),
            ("layer", _LAYER_KEYS),
        ):
            value = _first(data, keys)
            if value is not None and fact not in facts:
                facts[fact] = value
                facts["source"][fact] = name.rsplit("/", 1)[-1]
    if "width" not in facts and (m := _WIDTH.search(choice)):
        facts["width"] = _parse_width(m.group(1), m.group(2))
        facts["source"]["width"] = "folder name"
    if "layer" not in facts and (m := _LAYER.search(choice)):
        facts["layer"] = int(m.group(1))
        facts["source"]["layer"] = "folder name"
    return facts


# ── download ─────────────────────────────────────────────────────────────────


async def _token() -> str | None:
    try:
        from backend.modules.interpretability.tokenizer import hf_token

        return await hf_token()
    except Exception:  # noqa: BLE001 — a missing connector must not block public repos
        return None


async def download(entry_id: str, choice: str) -> AsyncIterator[dict[str, Any]]:
    """Fetch one choice of one catalog entry. Yields progress events, then `done`
    with the stored artifact, or an `error`."""
    e = catalog.entry(entry_id)
    if e is None:
        yield {"error": f"unknown catalog entry {entry_id!r}"}
        return
    token = await _token()
    try:
        choices = await catalog.browse(e, token)
    except httpx.HTTPError as exc:
        yield {"error": f"could not list {e.repo}: {exc}"}
        return
    picked = next((c for c in choices if c["id"] == choice), None)
    if picked is None:
        yield {"error": f"{choice or 'the repo'} is not a choice of {e.repo}"}
        return

    total = int(picked["bytes"])
    budget = budget_bytes()
    used = used_bytes()
    if budget and used + total > budget:
        yield {
            "error": (
                f"{total / 1024**3:.2f} GB would take the interpretability library to "
                f"{(used + total) / 1024**3:.2f} GB, over its {budget / 1024**3:.0f} GB "
                "budget. Delete an artifact or raise interp.libraryBudgetGb."
            )
        }
        return

    base = artifacts_root() / e.repo.replace("/", "__")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kind_path = "datasets/" if e.repoType == "dataset" else ""
    written: dict[str, Path] = {}
    completed = 0
    yield {"status": "downloading", "total": total, "completed": 0}
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        for f in picked["files"]:
            dest = base / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            part = dest.with_name(dest.name + ".part")
            url = f"{catalog.HF_ENDPOINT}/{kind_path}{e.repo}/resolve/main/{f['path']}"
            try:
                async with client.stream("GET", url, headers=headers) as res:
                    res.raise_for_status()
                    with open(part, "wb") as handle:
                        async for chunk in res.aiter_bytes(1 << 22):
                            handle.write(chunk)
                            completed += len(chunk)
                            yield {
                                "status": "downloading",
                                "total": total,
                                "completed": completed,
                                "file": f["path"],
                            }
            except httpx.HTTPError as exc:
                part.unlink(missing_ok=True)
                yield {"error": f"{f['path']}: {exc}"}
                return
            part.replace(dest)
            written[f["path"]] = dest

    folder = base / choice if choice else base
    record = {
        "id": artifact_id(entry_id, choice),
        "entry_id": entry_id,
        "kind": e.kind,
        "repo": e.repo,
        "choice": choice,
        "model_repo": picked["forModel"],
        "path": str(folder),
        "bytes": completed,
        "facts": json.dumps(facts_for(choice, written)),
        "created_at": time.time(),
    }
    with _db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO interp_artifacts
               (id, entry_id, kind, repo, choice, model_repo, path, bytes, facts, created_at)
               VALUES (:id, :entry_id, :kind, :repo, :choice, :model_repo, :path, :bytes, :facts, :created_at)""",
            record,
        )
    yield {"status": "done", "artifact": get_artifact(record["id"])}
