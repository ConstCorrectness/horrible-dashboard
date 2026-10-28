"""Fitted lenses: converting Neuronpedia's files into the llama.cpp lens slot, which
GGUFs a lens is offered for, and how the transport treats layers it has no map for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backend.modules.interp import env, lenses
from backend.modules.interp import worker as worker_mod
from backend.modules.llamacpp import lens

# ── the slot name ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "gemma-3-1b-it/jlens/Salesforce-wikitext/gemma-3-1b-it_jacobian_lens.pt",
            "np-jlens-salesforce-wikitext",
        ),
        (
            "qwen3-4b/jpp/Salesforce-wikitext/Qwen3-4B_jpp_lens.pt",
            "np-jpp-salesforce-wikitext",
        ),
    ],
)
def test_lens_ids_name_the_kind_and_dataset(path, expected):
    assert lenses.lens_id(Path(path)) == expected


# ── the transport ────────────────────────────────────────────────────────────


@pytest.fixture
def fitted(tmp_path) -> lens.LensSpec:
    d = tmp_path / "np-jlens-x"
    d.mkdir()
    np.save(d / "J_0.npy", np.array([[0.0, 1.0], [1.0, 0.0]], np.float16))  # swap
    (d / "lens.json").write_text(
        json.dumps({"kind": "jacobian", "layers": [0], "dModel": 2})
    )
    spec = lens._load_lens_dir(d)
    assert spec is not None
    return spec


def test_a_mapped_layer_is_transported(fitted):
    out = lens.transport(fitted, 0, np.array([1.0, 2.0], np.float32), final_layer=3)
    assert out is not None and out.tolist() == [2.0, 1.0]


def test_the_final_block_reads_through_identity(fitted):
    # A fitted lens maps every layer *to* the final one; the final's own map is I.
    h = np.array([5.0, 7.0], np.float32)
    assert lens.transport(fitted, 3, h, final_layer=3).tolist() == [5.0, 7.0]


def test_an_unmapped_layer_is_empty_not_borrowed(fitted):
    h = np.array([5.0, 7.0], np.float32)
    assert lens.transport(fitted, 2, h, final_layer=3) is None  # skipped by the fit
    assert lens.transport(fitted, -1, h, final_layer=3) is None  # the embedding


def test_identity_ignores_the_slot():
    h = np.array([1.0, 2.0], np.float32)
    assert lens.transport(lens.IDENTITY, 9, h).tolist() == [1.0, 2.0]


# ── which GGUFs a lens is offered for ────────────────────────────────────────

LENS_ART = {
    "id": "jacobian-lens:gemma-3-1b-it",
    "kind": "jlens",
    "repo": "neuronpedia/jacobian-lens",
    "modelRepo": "google/gemma-3-1b-it",
    "path": "x",
    "facts": {},
}


def _model(name: str, path: Path) -> SimpleNamespace:
    path.write_bytes(b"gguf")
    return SimpleNamespace(path=path, name=name, error="")


def test_only_matching_ggufs_are_offered(monkeypatch, tmp_path):
    from backend.modules.llamacpp import catalog

    models = [
        _model("Gemma 3 1b It", tmp_path / "gemma.gguf"),
        _model("whisper", tmp_path / "whisper.gguf"),  # not contradicted, not a fit
        _model("Qwen3", tmp_path / "qwen.gguf"),
    ]
    names = {
        str(tmp_path / "gemma.gguf"): {
            "name": "gemma-3-1b-it",
            "dModel": 1152,
            "layers": 26,
        },
        str(tmp_path / "whisper.gguf"): {
            "name": "whisper-base",
            "dModel": 512,
            "layers": 6,
        },
        str(tmp_path / "qwen.gguf"): {
            "name": "qwen3-0.6b",
            "dModel": 1024,
            "layers": 28,
        },
    }
    monkeypatch.setattr(catalog, "list_models", lambda: models)
    monkeypatch.setattr(
        lenses,
        "_target",
        lambda path, mtime: {"source": "gguf", "repo": None, **names[path]},
    )
    out = lenses.targets(LENS_ART)
    assert [t["name"] for t in out["targets"]] == ["Gemma 3 1b It"]
    assert out["unmatched"] == 2


def test_install_refuses_a_gguf_the_lens_does_not_match(monkeypatch, tmp_path):
    gguf = tmp_path / "whisper.gguf"
    gguf.write_bytes(b"gguf")
    monkeypatch.setattr(
        lenses,
        "_target",
        lambda path, mtime: {"source": "gguf", "repo": None, "name": "whisper-base"},
    )
    import asyncio

    with pytest.raises(lenses.LensInstallError, match="does not match"):
        asyncio.run(lenses.install(LENS_ART, str(gguf)))


# ── the conversion, in the worker ────────────────────────────────────────────


@pytest.fixture
def backend_python_worker(monkeypatch):
    monkeypatch.setattr(env, "python_path", lambda: Path(sys.executable))
    monkeypatch.setattr(env, "installed_groups", lambda: ["base"])
    w = worker_mod.Worker()
    w.start()
    yield w
    w.stop()


def test_the_worker_converts_a_lens_file_into_the_slot(tmp_path, backend_python_worker):
    torch = pytest.importorskip("torch")
    src = tmp_path / "m_jacobian_lens.pt"
    maps = {k: torch.eye(4, dtype=torch.float16) * (k + 1) for k in range(3)}
    torch.save(
        {"J": maps, "n_prompts": 7, "source_layers": [0, 1, 2], "d_model": 4}, src
    )
    dest = tmp_path / "slot" / "np-jlens-x"
    reply = backend_python_worker.request(
        "jlens_convert", {"src": str(src), "dest": str(dest), "label": "L"}, timeout=120
    )
    assert reply["state"] == "ran", reply
    assert reply["result"]["layers"] == [0, 1, 2] and reply["result"]["nPrompts"] == 7
    assert np.load(dest / "J_2.npy").dtype == np.float16
    assert float(np.load(dest / "J_2.npy")[0, 0]) == 3.0
    manifest = json.loads((dest / "lens.json").read_text())
    assert manifest["kind"] == "jacobian" and manifest["dModel"] == 4
    assert "l_out-k" in manifest["alignment"]
    # And the lens module reads what was written.
    spec = lens._load_lens_dir(dest)
    assert spec is not None and spec.layers == [0, 1, 2]


def test_a_file_without_maps_is_declined(tmp_path, backend_python_worker):
    torch = pytest.importorskip("torch")
    src = tmp_path / "not_a_lens.pt"
    torch.save({"weights": torch.zeros(2)}, src)
    reply = backend_python_worker.request(
        "jlens_convert", {"src": str(src), "dest": str(tmp_path / "out")}, timeout=120
    )
    assert reply["state"] == "not_checked" and "no `J` maps" in reply["reason"]
