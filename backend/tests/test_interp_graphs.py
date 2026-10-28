"""Attribution graphs: references, validation, storage, annotations, summaries.

The fixture is a miniature of Neuronpedia's Dallas → Austin graph: embeddings for
"Dallas" and "capital", a "Texas" feature and a "say a capital" feature, the
target logit " Austin", and one inhibitory edge that the summary must not follow.
"""

from __future__ import annotations

import json

import pytest

from backend.modules.interp import artifacts, graphs

TOKENS = ["<bos>", " capital", " of", " the", " state", " containing", " Dallas", " is"]


def _graph() -> dict:
    nodes = [
        {
            "node_id": "E_123_6",
            "layer": "E",
            "ctx_idx": 6,
            "feature_type": "embedding",
            "feature": 123,
        },
        {
            "node_id": "E_456_1",
            "layer": "E",
            "ctx_idx": 1,
            "feature_type": "embedding",
            "feature": 456,
        },
        {
            "node_id": "4_900_6",
            "layer": "4",
            "ctx_idx": 6,
            "feature_type": "cross layer transcoder",
            "feature": 900,
            "clerp": "Texas",
            "influence": 0.2,
            "activation": 3.1,
        },
        {
            "node_id": "15_77_7",
            "layer": "15",
            "ctx_idx": 7,
            "feature_type": "cross layer transcoder",
            "feature": 77,
            "clerp": "say a capital",
            "influence": 0.3,
            "activation": 5.0,
        },
        {
            "node_id": "9_5_7",
            "layer": "9",
            "ctx_idx": 7,
            "feature_type": "mlp reconstruction error",
            "feature": 5,
            "influence": 0.9,
        },
        {
            "node_id": "27_1001_7",
            "layer": "27",
            "ctx_idx": 7,
            "feature_type": "logit",
            "feature": 1001,
            "clerp": 'Output " Austin" (p=0.42)',
            "token_prob": 0.42,
            "is_target_logit": True,
        },
        {
            "node_id": "27_1002_7",
            "layer": "27",
            "ctx_idx": 7,
            "feature_type": "logit",
            "feature": 1002,
            "clerp": 'Output " Dallas" (p=0.05)',
            "token_prob": 0.05,
        },
    ]
    links = [
        {"source": "E_123_6", "target": "4_900_6", "weight": 0.9},
        {"source": "4_900_6", "target": "27_1001_7", "weight": 0.7},
        {"source": "E_456_1", "target": "15_77_7", "weight": 0.8},
        {"source": "15_77_7", "target": "27_1001_7", "weight": 0.6},
        {
            "source": "9_5_7",
            "target": "27_1001_7",
            "weight": -2.0,
        },  # inhibitory: not a path
    ]
    return {
        "metadata": {
            "slug": "mini-dallas",
            "scan": "gemma-2-2b",
            "prompt_tokens": TOKENS,
            "prompt": "".join(TOKENS),
        },
        "qParams": {"pinnedIds": [], "supernodes": []},
        "nodes": nodes,
        "links": links,
    }


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (
            "https://www.neuronpedia.org/gemma-2-2b/graph?slug=gemma-fact-dallas-austin&pinnedIds=1",
            ("gemma-2-2b", "gemma-fact-dallas-austin"),
        ),
        (
            "gemma-2-2b/gemma-fact-dallas-austin",
            ("gemma-2-2b", "gemma-fact-dallas-austin"),
        ),
        ("https://example.com/gemma-2-2b/graph?slug=x", None),
        ("https://www.neuronpedia.org/gemma-2-2b/graph", None),
        ("not a reference", None),
    ],
)
def test_references(ref, expected):
    assert graphs.parse_reference(ref) == expected


def test_validate_reads_the_facts():
    facts = graphs.validate(_graph())
    assert (facts["nodes"], facts["links"], facts["tokens"]) == (7, 5, 8)
    assert facts["target"] == 'Output " Austin" (p=0.42)'
    assert facts["kinds"]["logit"] == 2 and facts["danglingLinks"] == 0


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda g: g.pop("links"), "missing metadata, nodes or links"),
        (lambda g: g["metadata"].pop("prompt_tokens"), "prompt_tokens"),
        (lambda g: g["nodes"][0].pop("ctx_idx"), "ctx_idx"),
        (
            lambda g: g["links"].append({"source": "X", "target": "Y", "weight": 1}),
            "name nodes",
        ),
    ],
)
def test_validate_refuses_broken_graphs(mutate, message):
    g = _graph()
    mutate(g)
    with pytest.raises(graphs.GraphError, match=message):
        graphs.validate(g)


def test_import_stores_a_file_and_a_library_row():
    art = graphs.import_json(json.dumps(_graph()), "mini")
    assert art["kind"] == "graph" and art["facts"]["nodes"] == 7
    assert graphs.load(art)["metadata"]["slug"] == "mini-dallas"
    assert artifacts.delete_artifact(art["id"])
    assert not graphs.graphs_root().joinpath(art["choice"] + ".json").exists()


def test_qparams_keep_only_real_nodes():
    art = graphs.import_json(json.dumps(_graph()), "mini")
    q = graphs.save_qparams(
        art,
        {
            "pinnedIds": ["4_900_6", "nope"],
            "supernodes": [["Texas", "4_900_6", "ghost"], ["empty", "ghost"]],
        },
    )
    assert q["pinnedIds"] == ["4_900_6"]
    assert q["supernodes"] == [["Texas", "4_900_6"]]
    assert graphs.load(art)["qParams"]["pinnedIds"] == ["4_900_6"]


def test_summary_walks_the_strongest_positive_paths_to_the_target():
    s = graphs.summarize(_graph())
    assert s["target"] == 'Output " Austin" (p=0.42)'
    first, second = s["paths"][0]["path"], s["paths"][1]["path"]
    assert first == [
        "Emb ' Dallas'",
        "L4 F900 at ' Dallas' \"Texas\"",
        'Output " Austin" (p=0.42)',
    ]
    assert second[0] == "Emb ' capital'" and "say a capital" in second[1]
    # The inhibitory error edge is not a path into the answer.
    assert all("error" not in step for p in s["paths"] for step in p["path"])


def test_pinned_only_walks_the_readers_subgraph():
    g = _graph()
    g["qParams"]["pinnedIds"] = ["15_77_7"]
    s = graphs.summarize(g, pinned_only=True)
    assert s["pinnedOnly"] and len(s["paths"]) == 1
    assert "say a capital" in s["paths"][0]["path"][1]


def test_feature_index_comes_from_the_id():
    node = {
        "node_id": "20_15589_10",
        "feature": 2015589,
        "feature_type": "cross layer transcoder",
    }
    assert graphs.feature_index(node) == 15589
    assert graphs.feature_index({"node_id": "E_123_6", "feature": 123}) == 123


def test_the_weakest_link_is_the_real_minimum():
    s = graphs.summarize(_graph())
    # Dallas -> Texas (0.9) -> Austin (0.7): the weakest is 0.7, not a cap of 1.
    assert s["paths"][0]["weakestLink"] == 0.7
    assert "4_900_6" in s["pathNodes"]


def test_labels_fill_in_unlabelled_features():
    g = _graph()
    g["nodes"][3]["clerp"] = ""
    s = graphs.summarize(g, labels={"15_77_7": "capital cities"})
    assert any('"capital cities"' in step for p in s["paths"] for step in p["path"])
