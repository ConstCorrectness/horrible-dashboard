"""The interpretability library, its compatibility check, Neuronpedia and the worker.

Nothing here touches the network or a GPU: the Hub and Neuronpedia are mock
transports, and the worker test runs the real `worker_main.py` under the backend's
own interpreter with no model loaded, which is enough to see every reply state.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.modules.interp import artifacts, catalog, compat, env, neuronpedia
from backend.modules.interp import worker as worker_mod
from backend.modules.interp.routes import router


def _run(coro):
    return asyncio.run(coro)


def _collect(agen) -> list[dict[str, Any]]:
    async def go():
        return [event async for event in agen]

    return _run(go())


def _patch_async_client(monkeypatch, handler) -> list[httpx.Request]:
    """Route every `httpx.AsyncClient` through `handler`; returns the requests seen."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(record)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return seen


# ── facts ────────────────────────────────────────────────────────────────────


def test_facts_from_a_gemma_scope_folder_name():
    facts = artifacts.facts_for("resid_post/layer_13_width_16k_l0_medium", {})
    assert facts["width"] == 16 * 1024
    assert facts["layer"] == 13
    assert facts["source"] == {"width": "folder name", "layer": "folder name"}
    assert "dIn" not in facts  # a folder name never states the input width


def test_facts_from_a_config_win_over_the_folder_name(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"d_in": 1152, "d_sae": 65536, "hook_layer": 7}))
    facts = artifacts.facts_for(
        "resid_post/layer_13_width_16k_l0_medium",
        {"resid_post/layer_13_width_16k_l0_medium/config.json": config},
    )
    assert (facts["dIn"], facts["width"], facts["layer"]) == (1152, 65536, 7)
    assert facts["source"]["width"] == "config.json"


def test_facts_from_a_yaml_config(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text("d_model: 1024\nd_transcoder: 163840\n")
    facts = artifacts.facts_for("", {"config.yaml": config})
    assert (facts["dIn"], facts["width"]) == (1024, 163840)
    assert "layer" not in facts  # a per-layer set states no single layer


def test_an_unreadable_config_is_a_missing_fact_not_an_error(tmp_path: Path):
    config = tmp_path / "config.json"
    config.write_text("{not json")
    facts = artifacts.facts_for("layer_3_width_1m", {"config.json": config})
    assert facts["width"] == 1024**2 and facts["layer"] == 3


# ── compatibility ────────────────────────────────────────────────────────────

GEMMA_SAE = {
    "modelRepo": "google/gemma-3-1b-it",
    "facts": {"dIn": 1152, "layer": 13},
}


def test_compatible_when_the_repo_matches_and_nothing_contradicts():
    target = {
        "repo": "https://huggingface.co/google/gemma-3-1b-it",
        "dModel": 1152,
        "layers": 26,
    }
    assert compat.check(GEMMA_SAE, target) == {"state": "compatible", "reasons": []}


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        (
            {"repo": "google/gemma-3-1b-pt", "dModel": 1152, "layers": 26},
            "trained on google/gemma-3-1b-it",
        ),
        ({"repo": "google/gemma-3-1b-it", "dModel": 2560, "layers": 26}, "input width"),
        ({"repo": "google/gemma-3-1b-it", "dModel": 1152, "layers": 12}, "layer 13"),
    ],
)
def test_incompatible_names_the_contradiction(target, reason):
    verdict = compat.check(GEMMA_SAE, target)
    assert verdict["state"] == "incompatible"
    assert any(reason in r for r in verdict["reasons"])


def test_unknown_when_the_model_names_no_repo():
    # A width that fits is not enough: another model can share d_model.
    verdict = compat.check(GEMMA_SAE, {"repo": None, "dModel": 1152, "layers": 26})
    assert verdict["state"] == "unknown"
    assert compat.check(GEMMA_SAE, None)["state"] == "unknown"


def test_a_contradiction_wins_over_a_missing_repo():
    verdict = compat.check(GEMMA_SAE, {"repo": None, "dModel": 2048, "layers": 26})
    assert verdict["state"] == "incompatible"


GEMMA_IT_HEADER = {
    "general.architecture": "gemma3",
    "general.basename": "gemma-3",
    "general.size_label": "1B",
    "general.finetune": "it",
    # What ggml-org's real Gemma 3 1B IT GGUF states: the model it was tuned *from*.
    "general.base_model.count": 1,
    "general.base_model.0.repo_url": "https://huggingface.co/google/gemma-3-1b-pt",
    "gemma3.embedding_length": 1152,
    "gemma3.block_count": 26,
}


def test_a_gguf_is_identified_by_its_own_name_not_its_parent():
    target = compat.gguf_target(GEMMA_IT_HEADER, "D:\models\gemma-3-1b-it-Q8_0.gguf")
    assert target["label"] == "gemma-3-1b-it-Q8_0.gguf"
    assert target["repo"] is None  # base_model is the parent, not this file
    assert target["parents"] == ["https://huggingface.co/google/gemma-3-1b-pt"]
    assert (target["dModel"], target["layers"]) == (1152, 26)
    verdict = compat.check(GEMMA_SAE, target)
    assert verdict["state"] == "compatible"
    assert "own name" in verdict["reasons"][0]


def test_an_artifact_trained_on_the_parent_is_unknown_not_compatible():
    pt_sae = {**GEMMA_SAE, "modelRepo": "google/gemma-3-1b-pt"}
    target = compat.gguf_target(GEMMA_IT_HEADER, "gemma-3-1b-it-Q8_0.gguf")
    verdict = compat.check(pt_sae, target)
    assert verdict["state"] == "unknown"
    assert "fine-tuned from" in verdict["reasons"][0]


# ── catalog ──────────────────────────────────────────────────────────────────


def test_every_catalog_entry_is_well_formed():
    ids = [e.id for e in catalog.CATALOG]
    assert len(ids) == len(set(ids))
    for e in catalog.CATALOG:
        assert e.pick in ("folder", "repo")
        assert e.repoType in ("model", "dataset")
        assert e.forModel, e.id


def test_model_for_infers_the_org_of_a_per_model_folder():
    lens = catalog.entry("jacobian-lens")
    assert lens is not None
    assert catalog.model_for(lens, "gemma-3-1b-it") == "google/gemma-3-1b-it"
    assert catalog.model_for(lens, "qwen3-4b") == "Qwen/qwen3-4b"
    assert catalog.model_for(lens, "mystery-7b") == "mystery-7b"


def _scope_tree(request: httpx.Request) -> httpx.Response:
    """Gemma Scope's tree, split over two pages the way the Hub pages it."""
    page2 = "https://huggingface.co/api/models/google/gemma-scope-2-1b-it/tree/main/resid_post?cursor=2"
    if "cursor=2" in str(request.url):
        return httpx.Response(
            200,
            json=[
                {
                    "type": "file",
                    "path": "resid_post/layer_7_width_16k_l0_small/params.safetensors",
                    "size": 300,
                },
            ],
        )
    assert request.url.params.get("recursive") == "1"
    return httpx.Response(
        200,
        headers={"Link": f'<{page2}>; rel="next"'},
        json=[
            {"type": "directory", "path": "resid_post/layer_13_width_16k_l0_medium"},
            {
                "type": "file",
                "path": "resid_post/layer_13_width_16k_l0_medium/config.json",
                "size": 10,
            },
            {
                "type": "file",
                "path": "resid_post/layer_13_width_16k_l0_medium/params.safetensors",
                "size": 1000,
            },
            {
                "type": "file",
                "path": "resid_post/layer_13_width_16k_l0_medium/examples.safetensors",
                "size": 800_000,
            },
            {"type": "file", "path": "resid_post/README.md", "size": 5},
            {
                "type": "file",
                "path": "resid_post/layer_7_width_16k_l0_small/config.json",
                "size": 12,
            },
        ],
    )


def test_browse_groups_one_paged_listing_by_folder(monkeypatch):
    seen = _patch_async_client(monkeypatch, _scope_tree)
    entry = catalog.entry("gemma-scope-2-1b-it-resid")
    choices = _run(catalog.browse(entry, token="hf_test"))

    assert len(seen) == 2  # one listing, two pages — not one request per folder
    assert seen[0].headers["Authorization"] == "Bearer hf_test"
    by_id = {c["id"]: c for c in choices}
    assert set(by_id) == {
        "resid_post/layer_13_width_16k_l0_medium",
        "resid_post/layer_7_width_16k_l0_small",
    }
    l13 = by_id["resid_post/layer_13_width_16k_l0_medium"]
    # examples.safetensors is not in `include`: it stays on the Hub.
    assert [f["path"].rsplit("/", 1)[-1] for f in l13["files"]] == [
        "config.json",
        "params.safetensors",
    ]
    assert l13["bytes"] == 1010
    assert l13["forModel"] == "google/gemma-3-1b-it"
    assert by_id["resid_post/layer_7_width_16k_l0_small"]["bytes"] == 312


# ── download ─────────────────────────────────────────────────────────────────

L13 = "resid_post/layer_13_width_16k_l0_medium"


def _hub(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "/api/models/" in url:
        return _scope_tree(request)
    if url.endswith("config.json"):
        return httpx.Response(200, content=json.dumps({"d_in": 1152}).encode())
    if url.endswith("params.safetensors"):
        return httpx.Response(200, content=b"\0" * 1000)
    return httpx.Response(404)


def test_download_writes_files_and_records_their_facts(monkeypatch):
    _patch_async_client(monkeypatch, _hub)
    events = _collect(artifacts.download("gemma-scope-2-1b-it-resid", L13))

    assert events[-1]["status"] == "done", events[-1]
    art = events[-1]["artifact"]
    assert art["id"] == f"gemma-scope-2-1b-it-resid:{L13}"
    assert art["modelRepo"] == "google/gemma-3-1b-it"
    assert art["facts"]["dIn"] == 1152 and art["facts"]["layer"] == 13
    folder = Path(art["path"])
    assert sorted(p.name for p in folder.iterdir()) == [
        "config.json",
        "params.safetensors",
    ]
    assert not list(artifacts.artifacts_root().rglob("*.part"))
    assert artifacts.used_bytes() == art["bytes"]

    assert artifacts.delete_artifact(art["id"])
    assert not folder.exists() and artifacts.list_artifacts() == []


def test_a_failed_file_leaves_no_part_and_no_row(monkeypatch):
    def broken(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("params.safetensors"):
            return httpx.Response(500)
        return _hub(request)

    _patch_async_client(monkeypatch, broken)
    events = _collect(artifacts.download("gemma-scope-2-1b-it-resid", L13))
    assert "params.safetensors" in events[-1]["error"]
    assert not list(artifacts.artifacts_root().rglob("*.part"))
    assert artifacts.list_artifacts() == []


def test_download_refuses_over_budget_before_writing(monkeypatch):
    seen = _patch_async_client(monkeypatch, _hub)
    monkeypatch.setattr(artifacts, "budget_bytes", lambda: 500)
    events = _collect(artifacts.download("gemma-scope-2-1b-it-resid", L13))
    assert "budget" in events[0]["error"]
    assert all("/resolve/" not in str(r.url) for r in seen)


def test_the_route_refuses_a_size_the_user_did_not_confirm(monkeypatch):
    _patch_async_client(monkeypatch, _hub)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with TestClient(app) as client:
        res = client.post(
            "/api/interp/library/download",
            json={
                "entryId": "gemma-scope-2-1b-it-resid",
                "choice": L13,
                "confirmBytes": 999,
            },
        )
        assert res.status_code == 409
        assert "1010" in res.json()["detail"]

        res = client.post(
            "/api/interp/library/download",
            json={
                "entryId": "gemma-scope-2-1b-it-resid",
                "choice": L13,
                "confirmBytes": 1010,
            },
        )
        last = json.loads(res.text.strip().splitlines()[-1])
        assert last["status"] == "done"


# ── neuronpedia ──────────────────────────────────────────────────────────────

FEATURE = {
    "modelId": "gemma-3-1b-it",
    "layer": "13-gemmascope-2-res-16k",
    "index": "42",
    "explanations": [
        {"description": "the word Paris", "typeName": "oai_token-act-pair"}
    ],
    "pos_str": ["Paris", " France"],
    "neg_str": ["Berlin"],
    "maxActApprox": 31.5,
    "frac_nonzero": 0.0012,
    "activations": [
        {
            "tokens": [f"t{i}" for i in range(40)],
            "values": [0.0] * 30 + [9.0] + [0.0] * 9,
            "maxValue": 9.0,
            "maxValueTokenIndex": 30,
        }
    ]
    * 12,
}


def test_feature_is_trimmed_and_cached(monkeypatch):
    seen = _patch_async_client(monkeypatch, lambda r: httpx.Response(200, json=FEATURE))
    card = _run(neuronpedia.feature("gemma-3-1b-it", "13-gemmascope-2-res-16k", 42))

    assert card["explanations"] == [
        {"text": "the word Paris", "by": "oai_token-act-pair"}
    ]
    assert card["promotes"] == ["Paris", " France"] and card["cached"] is False
    assert len(card["examples"]) == neuronpedia.MAX_EXAMPLES
    example = card["examples"][0]
    # A window around the peak, with the peak re-indexed into it.
    assert example["tokens"][example["peak"]] == "t30"
    assert len(example["tokens"]) == 18

    again = _run(neuronpedia.feature("gemma-3-1b-it", "13-gemmascope-2-res-16k", 42))
    assert again["cached"] is True and len(seen) == 1


def test_sending_text_is_refused_unless_allowed(monkeypatch):
    with pytest.raises(neuronpedia.TextNotAllowed):
        neuronpedia.require_text_allowed()
    import backend.modules.settings.routes as settings_routes

    monkeypatch.setattr(
        settings_routes,
        "get_value",
        lambda key, default=None: key == "interp.neuronpedia.allowText" or default,
    )
    neuronpedia.require_text_allowed()


# ── worker ───────────────────────────────────────────────────────────────────


@pytest.fixture
def backend_python_worker(monkeypatch):
    """A worker that runs the real worker_main.py under this (torch-free) python."""
    monkeypatch.setattr(env, "python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(env, "installed_groups", lambda: ["base"])
    w = worker_mod.Worker()
    yield w
    w.stop()


def test_a_worker_with_no_env_answers_not_checked(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "python_path", lambda: tmp_path / "missing" / "python.exe")
    w = worker_mod.Worker()
    assert w.state() == "no_env"
    assert w.request("ping")["state"] == "not_checked"
    with pytest.raises(worker_mod.WorkerUnavailable):
        w.start()


def test_the_worker_carries_all_three_states(backend_python_worker):
    w = backend_python_worker
    w.start()
    assert w.state() == "ready"
    assert "generate" in w.describe()["ops"]

    # Declined: nothing is loaded, so nothing was attempted.
    reply = w.request("generate", {"prompt": "hi"}, timeout=30)
    assert reply == {
        "id": reply["id"],
        "state": "not_checked",
        "reason": "no model is loaded",
    }
    # Failed, with the traceback verbatim: no such dtype (or, where this interpreter
    # has no torch, no torch) — either way an exception inside the op.
    reply = w.request(
        "load",
        {"repo": "x/y", "dtype": "not_a_dtype", "allowDownload": True},
        timeout=120,
    )
    assert reply["state"] == "failed"
    assert reply["traceback"].startswith("Traceback")
    # Unknown ops are declined, not crashes.
    assert w.request("nope", timeout=30)["state"] == "not_checked"

    w.stop()
    assert w.state() == "stopped"


def test_a_worker_that_dies_fails_its_pending_requests(monkeypatch, tmp_path):
    script = tmp_path / "dies.py"
    script.write_text(
        "import json, sys\n"
        "print(json.dumps({'event': 'ready', 'ops': ['x']}), flush=True)\n"
        "sys.stdin.readline()\n"
        "sys.exit(3)\n"
    )
    monkeypatch.setattr(env, "python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(env, "installed_groups", lambda: ["base"])
    monkeypatch.setattr(worker_mod, "WORKER_SCRIPT", script)
    w = worker_mod.Worker()
    w.start()
    reply = w.request("x", timeout=30)
    assert reply["state"] == "failed" and "code 3" in reply["traceback"]
    assert w.state() == "crashed"
    w.stop()
