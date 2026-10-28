"""Attribution graphs: import, validate, keep, and summarise them.

An attribution graph (Anthropic's circuit tracing; `circuit-tracer`; Neuronpedia's
graph viewer) explains one prediction as a network: embedding nodes at the
bottom, transcoder features in between, the output logits on top, and signed
edges carrying how much each node's activation pushed each other node. The
format is circuit-tracer's JSON, the one Neuronpedia serves:

- `metadata` — the prompt's tokens, the model (`scan`), how it was generated.
- `nodes[]` — `node_id` (`layer_feature_position`; `E_token_position` for
  embeddings), `layer` (`"E"` for embeddings, `n_layers` for logits),
  `feature_type`, `ctx_idx`, `influence`, `activation`, `clerp` (a label,
  often empty), and for logits `token_prob` / `is_target_logit`.
- `links[]` — `{source, target, weight}`, signed.
- `qParams` — the reader's annotations: pinned nodes, supernodes (`[label, *ids]`).

Neuronpedia graphs are imported by URL or `model/slug` (a public GET of public
data); a local circuit-tracer run is the same file. Each is validated before it
is kept — a graph whose links name nodes it does not have would draw edges to
nowhere — and stored as an `interp_artifacts` row (kind `graph`) beside its JSON
in `$DATA/interp/graphs/`. Annotations are written back into the file's own
`qParams`, so an exported graph opens on Neuronpedia the way it was left.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from backend import paths
from backend.modules.interp import artifacts

#: Refuse anything bigger: a graph is a few MB; a 500 MB "graph" is something else.
MAX_GRAPH_BYTES = 64 * 1024 * 1024
NEURONPEDIA = "https://www.neuronpedia.org"
REQUIRED_NODE_KEYS = ("node_id", "layer", "ctx_idx", "feature_type")


class GraphError(Exception):
    """The graph cannot be imported or read — a reason for the reader."""


def graphs_root() -> Path:
    return paths.data_dir() / "interp" / "graphs"


# ── where a graph comes from ────────────────────────────────────────────────


def parse_reference(ref: str) -> tuple[str, str] | None:
    """`(model, slug)` from a Neuronpedia graph page URL or `model/slug`."""
    ref = ref.strip()
    if ref.startswith("http"):
        u = urlparse(ref)
        if "neuronpedia.org" not in u.netloc:
            return None
        parts = [p for p in u.path.split("/") if p]
        slug = (parse_qs(u.query).get("slug") or [""])[0]
        if len(parts) >= 2 and parts[1] == "graph" and slug:
            return parts[0], slug
        return None
    m = re.fullmatch(r"([\w.\-]+)/([\w.\-]+)", ref)
    return (m.group(1), m.group(2)) if m else None


def validate(graph: Any) -> dict[str, Any]:
    """Facts about a graph, or `GraphError` naming what is wrong with it."""
    if not isinstance(graph, dict):
        raise GraphError("a graph is a JSON object with metadata, nodes and links")
    meta, nodes, links = graph.get("metadata"), graph.get("nodes"), graph.get("links")
    if (
        not isinstance(meta, dict)
        or not isinstance(nodes, list)
        or not isinstance(links, list)
    ):
        raise GraphError("missing metadata, nodes or links")
    tokens = meta.get("prompt_tokens")
    if not isinstance(tokens, list) or not tokens:
        raise GraphError(
            "metadata.prompt_tokens is missing — the graph has no prompt to lay out"
        )
    ids: set[str] = set()
    kinds: dict[str, int] = {}
    for i, node in enumerate(nodes):
        missing = [k for k in REQUIRED_NODE_KEYS if k not in node]
        if missing:
            raise GraphError(f"node {i} has no {', '.join(missing)}")
        ids.add(str(node["node_id"]))
        kind = str(node["feature_type"])
        kinds[kind] = kinds.get(kind, 0) + 1
    dangling = 0
    for link in links:
        if "source" not in link or "target" not in link or "weight" not in link:
            raise GraphError("a link has no source, target or weight")
        if str(link["source"]) not in ids or str(link["target"]) not in ids:
            dangling += 1
    if dangling and dangling > 0.01 * max(len(links), 1):
        raise GraphError(
            f"{dangling} of {len(links)} links name nodes the graph does not have"
        )
    logits = [n for n in nodes if n["feature_type"] == "logit"]
    target = next((n for n in logits if n.get("is_target_logit")), None) or max(
        logits, key=lambda n: float(n.get("token_prob") or 0), default=None
    )
    return {
        "model": meta.get("scan"),
        "prompt": meta.get("prompt") or "".join(str(t) for t in tokens),
        "tokens": len(tokens),
        "nodes": len(nodes),
        "links": len(links),
        "danglingLinks": dangling,
        "kinds": kinds,
        "target": target.get("clerp") if target else None,
        "generator": ((meta.get("info") or {}).get("generator") or {}).get("name"),
    }


async def _neuronpedia_graph(model: str, slug: str) -> tuple[dict[str, Any], bytes]:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        res = await client.get(
            f"{NEURONPEDIA}/api/graph/{model}/{slug}",
            headers={"Accept": "application/json"},
        )
        if res.status_code == 404:
            raise GraphError(f"Neuronpedia has no graph {model}/{slug}")
        res.raise_for_status()
        page = res.json()
        url = page.get("url")
        if not url:
            raise GraphError("Neuronpedia's answer names no graph file")
        async with client.stream("GET", url) as body:
            body.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in body.aiter_bytes(1 << 20):
                size += len(chunk)
                if size > MAX_GRAPH_BYTES:
                    raise GraphError(
                        f"the graph file is over {MAX_GRAPH_BYTES >> 20} MB"
                    )
                chunks.append(chunk)
    return page, b"".join(chunks)


def _store(
    graph: dict[str, Any], facts: dict[str, Any], source: dict[str, Any]
) -> dict[str, Any]:
    slug = str(
        source.get("slug")
        or (graph["metadata"].get("slug"))
        or f"graph-{int(time.time())}"
    )
    model = str(source.get("model") or graph["metadata"].get("scan") or "local")
    safe = re.sub(r"[^\w.\-]+", "-", f"{model}--{slug}").strip("-")
    path = graphs_root() / f"{safe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(graph), encoding="utf-8")
    tmp.replace(path)
    artifact_id = f"graph:{safe}"
    record = {
        "id": artifact_id,
        "entry_id": "graph",
        "kind": "graph",
        "repo": source.get("origin") or "local",
        "choice": safe,
        "model_repo": model,
        "path": str(path),
        "bytes": path.stat().st_size,
        "facts": json.dumps(
            {**facts, "source": {}, "sourceSet": source.get("sourceSet")}
        ),
        "created_at": time.time(),
    }
    with artifacts._db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO interp_artifacts
               (id, entry_id, kind, repo, choice, model_repo, path, bytes, facts, created_at)
               VALUES (:id, :entry_id, :kind, :repo, :choice, :model_repo, :path, :bytes, :facts, :created_at)""",
            record,
        )
    stored = artifacts.get_artifact(artifact_id)
    assert stored is not None
    return stored


async def import_reference(ref: str) -> dict[str, Any]:
    """Import a Neuronpedia graph by page URL or `model/slug`."""
    parsed = parse_reference(ref)
    if parsed is None:
        raise GraphError(
            "not a Neuronpedia graph reference — paste a neuronpedia.org/<model>/graph?slug=… "
            "URL or <model>/<slug>"
        )
    model, slug = parsed
    page, raw = await _neuronpedia_graph(model, slug)
    try:
        graph = json.loads(raw)
    except ValueError as exc:
        raise GraphError(f"the graph file is not JSON: {exc}") from exc
    facts = validate(graph)
    return _store(
        graph,
        facts,
        {
            "slug": slug,
            "model": model,
            "origin": "neuronpedia",
            # Labels for nodes live on Neuronpedia under `{layer}-{sourceSet}`.
            "sourceSet": page.get("sourceSetName"),
        },
    )


def import_json(raw: str | bytes, name: str = "") -> dict[str, Any]:
    """Import a graph file — a local circuit-tracer run, or a saved Neuronpedia one."""
    if len(raw) > MAX_GRAPH_BYTES:
        raise GraphError(f"the graph is over {MAX_GRAPH_BYTES >> 20} MB")
    try:
        graph = json.loads(raw)
    except ValueError as exc:
        raise GraphError(f"not JSON: {exc}") from exc
    facts = validate(graph)
    return _store(graph, facts, {"slug": name or None, "origin": "file"})


# ── reading one ──────────────────────────────────────────────────────────────


def load(artifact: dict[str, Any]) -> dict[str, Any]:
    path = Path(artifact["path"])
    if not path.is_file():
        raise GraphError(f"the graph file is gone: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_qparams(artifact: dict[str, Any], qparams: dict[str, Any]) -> dict[str, Any]:
    """Write the reader's pins and supernodes into the graph's own `qParams`."""
    graph = load(artifact)
    ids = {str(n["node_id"]) for n in graph["nodes"]}
    pinned = [str(i) for i in qparams.get("pinnedIds") or [] if str(i) in ids]
    supernodes = []
    for group in qparams.get("supernodes") or []:
        if isinstance(group, list) and len(group) >= 2:
            members = [str(i) for i in group[1:] if str(i) in ids]
            if members:
                supernodes.append([str(group[0]), *members])
    graph["qParams"] = {
        **(graph.get("qParams") or {}),
        "pinnedIds": pinned,
        "supernodes": supernodes,
    }
    path = Path(artifact["path"])
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(graph), encoding="utf-8")
    tmp.replace(path)
    return graph["qParams"]


def feature_index(node: dict[str, Any]) -> int | None:
    """A transcoder node's feature index within its layer.

    The id's middle part (`20_15589_10` → 15589). Some generators put an encoded
    `layer * 100000 + index` in `feature` (Neuronpedia's 2015589 for that node),
    and a Neuronpedia lookup by that number asks for a feature that does not exist.
    """
    parts = str(node.get("node_id") or "").split("_")
    if len(parts) == 3 and parts[0] != "E":
        try:
            return int(parts[1])
        except ValueError:
            return None
    feature = node.get("feature")
    return int(feature) if isinstance(feature, int) else None


def summarize(
    graph: dict[str, Any],
    *,
    pinned_only: bool = False,
    paths_wanted: int = 5,
    depth: int = 6,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The strongest paths into the target logit, as words — what lets an agent
    narrate a circuit. Greedy: from the target, follow the strongest positive
    incoming edge back toward the embeddings, `paths_wanted` times, never reusing
    an edge. Error nodes are not steps: they are what the transcoders failed to
    explain, and a path through one explains nothing. Pinned-only restricts the
    walk to the reader's subgraph. `labels` (node id → text) fills in features
    whose own `clerp` is empty — Neuronpedia's explanations, fetched by the route.
    `pathNodes` lists the ids walked, so a caller knows which labels it needs."""
    labels = labels or {}
    nodes = {str(n["node_id"]): n for n in graph["nodes"]}
    pinned = set((graph.get("qParams") or {}).get("pinnedIds") or [])

    def allowed(nid: str) -> bool:
        kind = nodes[nid]["feature_type"]
        if "error" in kind:
            return False
        if pinned_only and pinned:
            return nid in pinned or kind in ("embedding", "logit")
        return True

    incoming: dict[str, list[tuple[float, str]]] = {}
    for link in graph["links"]:
        s, t, w = str(link["source"]), str(link["target"]), float(link["weight"])
        if s in nodes and t in nodes and allowed(s) and allowed(t):
            incoming.setdefault(t, []).append((w, s))
    for edges in incoming.values():
        edges.sort(reverse=True)
    tokens = graph["metadata"]["prompt_tokens"]

    def label(nid: str) -> str:
        n = nodes[nid]
        kind = n["feature_type"]
        pos = int(n.get("ctx_idx") or 0)
        tok = tokens[pos] if 0 <= pos < len(tokens) else "?"
        if kind == "embedding":
            return f"Emb {tok!r}"
        if kind == "logit":
            return n.get("clerp") or f"logit {n.get('feature')}"
        if "error" in kind:
            return f"error L{n['layer']} at {tok!r}"
        text = n.get("clerp") or labels.get(nid)
        return f"L{n['layer']} F{feature_index(n)} at {tok!r}" + (
            f' "{text}"' if text else ""
        )

    logits = [n for n in graph["nodes"] if n["feature_type"] == "logit"]
    target = next((n for n in logits if n.get("is_target_logit")), None) or max(
        logits, key=lambda n: float(n.get("token_prob") or 0), default=None
    )
    if target is None:
        return {"error": "the graph has no logit nodes"}
    used: set[tuple[str, str]] = set()
    paths_out: list[dict[str, Any]] = []
    walked: list[str] = []
    for _ in range(paths_wanted):
        cur = str(target["node_id"])
        chain = [cur]
        weakest = math.inf
        for _step in range(depth):
            nxt = next(
                (
                    (w, s)
                    for w, s in incoming.get(cur, [])
                    if (s, cur) not in used and w > 0
                ),
                None,
            )
            if nxt is None:
                break
            used.add((nxt[1], cur))
            weakest = min(weakest, nxt[0])
            cur = nxt[1]
            chain.append(cur)
            if nodes[cur]["feature_type"] == "embedding":
                break
        if len(chain) > 1:
            walked.extend(chain)
            paths_out.append(
                {
                    "path": [label(n) for n in reversed(chain)],
                    "weakestLink": round(weakest, 4),
                }
            )
    return {
        "prompt": graph["metadata"].get("prompt"),
        "target": label(str(target["node_id"])),
        "targetProb": target.get("token_prob"),
        "pinnedOnly": bool(pinned_only and pinned),
        "paths": paths_out,
        "pathNodes": list(dict.fromkeys(walked)),
        "supernodes": [
            {"label": g[0], "members": [label(m) for m in g[1:] if m in nodes]}
            for g in (graph.get("qParams") or {}).get("supernodes") or []
        ],
    }


async def neuronpedia_labels(
    graph: dict[str, Any], node_ids: list[str], source_set: str | None
) -> dict[str, str]:
    """Neuronpedia's explanations for transcoder features with no `clerp` of their
    own (public GETs by id, cached a week). Empty when the graph's source set is
    unknown — a local graph has none to ask about."""
    from backend.modules.interp import neuronpedia

    model = graph["metadata"].get("scan")
    if not model or not source_set:
        return {}
    nodes = {str(n["node_id"]): n for n in graph["nodes"]}
    wanted = [
        nid
        for nid in node_ids
        if nid in nodes
        and "transcoder" in str(nodes[nid]["feature_type"])
        and not nodes[nid].get("clerp")
        and feature_index(nodes[nid]) is not None
    ]

    async def one(nid: str) -> tuple[str, str | None]:
        n = nodes[nid]
        try:
            card = await neuronpedia.feature(
                str(model), f"{n['layer']}-{source_set}", feature_index(n) or 0
            )
        except Exception:  # noqa: BLE001 — an unlabelled step is still a step
            return nid, None
        explanations = card.get("explanations") or []
        return nid, explanations[0]["text"] if explanations else None

    results = await asyncio.gather(*(one(nid) for nid in wanted[:40]))
    return {nid: text for nid, text in results if text}
