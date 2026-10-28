"""The interpretability artifacts this node knows how to fetch, and what is in them.

A curated list rather than free search: an SAE, a transcoder set or a Jacobian lens
is only useful beside the exact model it was trained on, and the repos that publish
them are laid out each their own way. Every entry here was checked against the Hub
(2026-10-27) and says, for each, how a downloadable *choice* is formed:

- `pick: "folder"` — the entry's `root` holds one folder per choice (Gemma Scope 2's
  `resid_post/layer_13_width_16k_l0_medium`, one Jacobian lens per model). `include`
  names the files that make a usable artifact; the rest of the folder (Gemma Scope's
  ~800 MB `examples.safetensors`) is left on the Hub.
- `pick: "repo"` — the whole repo is one artifact (a transcoder set: one file per
  layer, all needed for an attribution graph).

`browse` lists the choices with their real sizes from the Hub's tree API, so nothing
is downloaded before its size has been shown.
"""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

HF_ENDPOINT = "https://huggingface.co"


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    kind: str  # sae | transcoder | clt | jlens | axis
    title: str
    repo: str
    repoType: str  # model | dataset
    pick: str  # folder | repo
    root: str = ""
    include: tuple[str, ...] = ("*",)
    #: The Hugging Face model this was trained on. `{choice}` stands for a picked
    #: folder name when the model differs per folder (one lens per model).
    forModel: str = ""
    note: str = ""


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        id="gemma-scope-2-1b-it-resid",
        kind="sae",
        title="Gemma Scope 2 · Gemma 3 1B IT · residual stream SAEs",
        repo="google/gemma-scope-2-1b-it",
        repoType="model",
        pick="folder",
        root="resid_post",
        include=("config.json", "params.safetensors"),
        forModel="google/gemma-3-1b-it",
        note="JumpReLU SAEs at 25/50/65/85% depth; 64k or 262k width with medium L0 is DeepMind's recommendation.",
    ),
    CatalogEntry(
        id="gemma-scope-2-4b-it-resid",
        kind="sae",
        title="Gemma Scope 2 · Gemma 3 4B IT · residual stream SAEs",
        repo="google/gemma-scope-2-4b-it",
        repoType="model",
        pick="folder",
        root="resid_post",
        include=("config.json", "params.safetensors"),
        forModel="google/gemma-3-4b-it",
    ),
    CatalogEntry(
        id="gemma-scope-2-1b-it-transcoder",
        kind="transcoder",
        title="Gemma Scope 2 · Gemma 3 1B IT · transcoders",
        repo="google/gemma-scope-2-1b-it",
        repoType="model",
        pick="folder",
        root="transcoder",
        include=("config.json", "params.safetensors"),
        forModel="google/gemma-3-1b-it",
    ),
    CatalogEntry(
        id="qwen3-0.6b-transcoders",
        kind="transcoder",
        title="Qwen3 0.6B · per-layer transcoders (circuit-tracer)",
        repo="mwhanna/qwen3-0.6b-transcoders-lowl0",
        repoType="model",
        pick="repo",
        include=("config.yaml", "layer_*.safetensors"),
        forModel="Qwen/Qwen3-0.6B",
        note="One file per layer; an attribution graph needs all of them.",
    ),
    CatalogEntry(
        id="llama-3.2-1b-transcoders",
        kind="transcoder",
        title="Llama 3.2 1B · per-layer transcoders (circuit-tracer)",
        repo="mntss/transcoder-Llama-3.2-1B",
        repoType="model",
        pick="repo",
        include=("config.yaml", "layer_*.safetensors"),
        forModel="meta-llama/Llama-3.2-1B",
        note="Trained on the base model; the IT model's residuals are close but not identical.",
    ),
    CatalogEntry(
        id="jacobian-lens",
        kind="jlens",
        title="Jacobian lenses (J-lens / J++) · per model",
        repo="neuronpedia/jacobian-lens",
        repoType="model",
        pick="folder",
        root="",
        include=("*.pt", "*.yaml", "*.json"),
        forModel="{choice}",
        note="Folder names are Neuronpedia model ids (gemma-3-1b-it, qwen3-4b, …).",
    ),
    CatalogEntry(
        id="assistant-axis",
        kind="axis",
        title="Assistant Axis vectors and capping configs",
        repo="lu-christina/assistant-axis-vectors",
        repoType="dataset",
        pick="folder",
        root="",
        include=("*.pt",),
        forModel="{choice}",
        note="Published for Gemma 2 27B, Qwen3 32B and Llama 3.3 70B — larger than this GPU.",
    ),
)

#: Neuronpedia's model ids → Hugging Face orgs, for entries keyed by folder name.
_ORG = {
    "gemma": "google",
    "qwen": "Qwen",
    "llama": "meta-llama",
    "olmo": "allenai",
    "deepseek": "deepseek-ai",
}


def entry(entry_id: str) -> CatalogEntry | None:
    return next((e for e in CATALOG if e.id == entry_id), None)


def model_for(e: CatalogEntry, choice: str) -> str:
    """The HF model an artifact was trained on. For a per-model folder it is
    inferred from the folder name — and is a guess the compatibility check labels."""
    if e.forModel != "{choice}":
        return e.forModel
    name = choice.rsplit("/", 1)[-1]
    org = next((o for prefix, o in _ORG.items() if name.lower().startswith(prefix)), "")
    return f"{org}/{name}" if org else name


def _api(e: CatalogEntry) -> str:
    return f"{HF_ENDPOINT}/api/{'datasets' if e.repoType == 'dataset' else 'models'}/{e.repo}"


async def _tree(
    client: httpx.AsyncClient,
    e: CatalogEntry,
    path: str,
    token: str | None,
    recursive: bool,
) -> list[dict[str, Any]]:
    """One tree listing, following the Hub's `Link: rel="next"` pages — a recursive
    listing of Gemma Scope's `resid_post` runs past the 1000-entry page size."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url: str | None = f"{_api(e)}/tree/main" + (f"/{path}" if path else "")
    params: dict[str, str] | None = {"recursive": "1"} if recursive else None
    out: list[dict[str, Any]] = []
    while url:
        res = await client.get(url, params=params, headers=headers)
        res.raise_for_status()
        data = res.json()
        out.extend(data if isinstance(data, list) else [])
        url = res.links.get("next", {}).get("url")
        params = None  # the next link carries its own query
    return out


def _wanted(e: CatalogEntry, path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pattern) for pattern in e.include)


@dataclass
class Choice:
    id: str  # the folder path, or "" for a whole repo
    files: list[dict[str, Any]] = field(default_factory=list)
    bytes: int = 0
    forModel: str = ""


async def browse(
    e: CatalogEntry, token: str | None, limit: int = 200
) -> list[dict[str, Any]]:
    """The downloadable choices of one entry, each with the files it would fetch
    and their total size. Read from the Hub; nothing is downloaded."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        if e.pick == "repo":
            files = [
                {"path": f["path"], "size": int(f.get("size") or 0)}
                for f in await _tree(client, e, "", token, recursive=False)
                if f.get("type") == "file" and _wanted(e, f["path"])
            ]
            choice = Choice(
                id="",
                files=files,
                bytes=sum(f["size"] for f in files),
                forModel=model_for(e, ""),
            )
            return [asdict(choice)]

        # One recursive listing of the root, grouped by the folder directly under
        # it — one request (paged), not one per folder.
        prefix = f"{e.root}/" if e.root else ""
        groups: dict[str, list[dict[str, Any]]] = {}
        for f in await _tree(client, e, e.root, token, recursive=True):
            path = str(f.get("path") or "")
            if (
                f.get("type") != "file"
                or not path.startswith(prefix)
                or not _wanted(e, path)
            ):
                continue
            rest = path[len(prefix) :]
            if "/" not in rest:
                continue  # a file at the root is not a choice
            folder = prefix + rest.split("/", 1)[0]
            groups.setdefault(folder, []).append(
                {"path": path, "size": int(f.get("size") or 0)}
            )
        out: list[dict[str, Any]] = []
        for folder in sorted(groups)[:limit]:
            files = groups[folder]
            out.append(
                asdict(
                    Choice(
                        id=folder,
                        files=files,
                        bytes=sum(f["size"] for f in files),
                        forModel=model_for(e, folder),
                    )
                )
            )
        return out
