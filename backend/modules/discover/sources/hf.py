"""Hugging Face Hub: models, datasets and Spaces.

Through the HF connector's `hub_get` / `read_text`, which authenticate when connected
and read public repos anonymously otherwise — browsing the Hub never needed an
account, and the pane used to refuse to work without one.

Accuracy notes that shape this file:

- The Hub's `downloads` is a **30-day** count. `downloadsAllTime` is asked for via
  `expand[]` and shown beside it, each labelled for what it is.
- Pagination is a `Link: <…cursor=…>; rel="next"` header, not an offset. The cursor
  is passed back verbatim.
- The default feed is `sort=trendingScore` — the Hub's own trending ranking, the
  same list its homepage shows — not a guess at "popular".
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from backend.modules.connectors.providers import huggingface, huggingface_tools
from backend.modules.discover.models import (
    Badge,
    DiscoverDetail,
    DiscoverItem,
    Fact,
    FilterSpec,
    KindSpec,
    Link,
    Metric,
    Option,
    SourceSpec,
)
from backend.modules.discover.sources.base import (
    PAGE_SIZE,
    Query,
    SourceResult,
    SourceUnavailable,
    clip,
    num,
    strip_front_matter,
)

HUB = "https://huggingface.co"
_PATHS = {"model": "models", "dataset": "datasets", "space": "spaces"}
_URL_PREFIX = {"model": "", "dataset": "datasets/", "space": "spaces/"}

_EXPAND = {
    "model": [
        "downloads",
        "downloadsAllTime",
        "likes",
        "trendingScore",
        "pipeline_tag",
        "library_name",
        "gated",
        "private",
        "tags",
        "createdAt",
        "lastModified",
    ],
    "dataset": [
        "downloads",
        "downloadsAllTime",
        "likes",
        "trendingScore",
        "gated",
        "private",
        "tags",
        "description",
        "createdAt",
        "lastModified",
    ],
    "space": [
        "likes",
        "trendingScore",
        "sdk",
        "runtime",
        "cardData",
        "private",
        "tags",
        "createdAt",
        "lastModified",
    ],
}

_SORT_LABEL = {
    "trendingScore": "Trending",
    "downloads": "Downloads · 30d",
    "likes": "Likes",
    "lastModified": "Recently updated",
    "createdAt": "Newest",
}

_TASKS = [
    ("", "Any task"),
    ("text-generation", "Text generation"),
    ("image-text-to-text", "Image-text-to-text"),
    ("text-to-image", "Text-to-image"),
    ("text-to-speech", "Text-to-speech"),
    ("automatic-speech-recognition", "Speech recognition"),
    ("feature-extraction", "Embeddings"),
    ("text-classification", "Text classification"),
    ("image-classification", "Image classification"),
    ("object-detection", "Object detection"),
    ("text-to-video", "Text-to-video"),
    ("reinforcement-learning", "Reinforcement learning"),
]
_LIBRARIES = [
    ("", "Any library"),
    ("transformers", "Transformers"),
    ("gguf", "GGUF"),
    ("mlx", "MLX"),
    ("diffusers", "Diffusers"),
    ("sentence-transformers", "Sentence Transformers"),
    ("peft", "PEFT"),
    ("onnx", "ONNX"),
]
_MODALITIES = [
    ("", "Any modality"),
    ("text", "Text"),
    ("image", "Image"),
    ("audio", "Audio"),
    ("video", "Video"),
    ("tabular", "Tabular"),
    ("3d", "3D"),
]
_SDKS = [
    ("", "Any SDK"),
    ("gradio", "Gradio"),
    ("streamlit", "Streamlit"),
    ("docker", "Docker"),
    ("static", "Static"),
]

_STAGE_TONE = {
    "RUNNING": "ok",
    "RUNNING_BUILDING": "ok",
    "BUILDING": "info",
    "SLEEPING": "idle",
    "PAUSED": "idle",
    "STOPPED": "idle",
    "BUILD_ERROR": "fail",
    "RUNTIME_ERROR": "fail",
    "CONFIG_ERROR": "fail",
}


def _opts(pairs: list[tuple[str, str]]) -> list[Option]:
    return [Option(value=v, label=label) for v, label in pairs]


def _sorts(kind: str) -> list[Option]:
    keys = ["trendingScore", "likes", "lastModified", "createdAt"]
    if kind != "space":
        keys.insert(1, "downloads")
    return [Option(value=k, label=_SORT_LABEL[k]) for k in keys]


def next_cursor(headers: dict[str, str]) -> str | None:
    """The `cursor` query value from a `Link: <…>; rel="next"` header."""
    link = next((v for k, v in headers.items() if k.lower() == "link"), "")
    match = re.search(r'<([^>]+)>\s*;\s*rel="next"', link)
    if not match:
        return None
    values = parse_qs(urlparse(match.group(1)).query).get("cursor")
    return values[0] if values else None


def rate_limit_reset(headers: dict[str, str]) -> float | None:
    """Seconds until the window resets, from `RateLimit: "api";r=0;t=175`."""
    raw = next((v for k, v in headers.items() if k.lower() == "ratelimit"), "")
    match = re.search(r"\bt=(\d+)", raw)
    return float(match.group(1)) if match else None


def _license(tags: list[str]) -> str | None:
    for tag in tags:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _gated_badge(gated: Any) -> Badge | None:
    # `False`/absent is open; `None` would be "unstated", and the Hub always states it
    # when asked through `expand[]`, so only truthy values badge.
    if gated in (None, False):
        return None
    mode = gated if isinstance(gated, str) else ""
    return Badge(
        label=f"gated{f' · {mode}' if mode else ''}",
        tone="warn",
        title="A licence must be accepted on the Hub before this repo can be downloaded.",
    )


def to_item(row: dict[str, Any], kind: str) -> DiscoverItem:
    repo_id = str(row.get("id") or "")
    tags = [t for t in (row.get("tags") or []) if isinstance(t, str)]
    badges: list[Badge] = []
    if gated := _gated_badge(row.get("gated")):
        badges.append(gated)
    if row.get("private"):
        badges.append(Badge(label="private", tone="info"))
    if lic := _license(tags):
        badges.append(Badge(label=lic))

    metrics: list[Metric] = []
    subtitle = ""
    description = ""
    if kind in ("model", "dataset"):
        metrics += [
            Metric(
                key="downloads",
                label="downloads · 30d",
                value=num(row.get("downloads")),
            ),
            Metric(
                key="downloadsAllTime",
                label="downloads · all time",
                value=num(row.get("downloadsAllTime")),
            ),
        ]
    metrics += [
        Metric(key="likes", label="likes", value=num(row.get("likes"))),
        Metric(
            key="trending",
            label="trending score",
            value=num(row.get("trendingScore")),
            unit="score",
        ),
    ]
    if kind == "model":
        subtitle = " · ".join(
            x for x in (row.get("pipeline_tag"), row.get("library_name")) if x
        )
    elif kind == "dataset":
        description = clip(row.get("description"), 280)
        modalities = [t.split(":", 1)[1] for t in tags if t.startswith("modality:")]
        subtitle = " · ".join(modalities[:3])
    else:
        card = row.get("cardData") or {}
        description = clip(
            card.get("short_description") or card.get("title") or "", 200
        )
        sdk = row.get("sdk") or card.get("sdk")
        runtime = row.get("runtime") or {}
        stage = str(runtime.get("stage") or "")
        hardware = ((runtime.get("hardware") or {}).get("current")) or ""
        subtitle = " · ".join(x for x in (sdk, hardware) if x)
        if stage:
            badges.insert(
                0,
                Badge(
                    label=stage.lower().replace("_", " "),
                    tone=_STAGE_TONE.get(stage, "idle"),  # type: ignore[arg-type]
                ),
            )
    return DiscoverItem(
        source="hf",
        kind=kind,
        id=repo_id,
        title=repo_id,
        subtitle=subtitle,
        description=description,
        url=f"{HUB}/{_URL_PREFIX[kind]}{repo_id}",
        author=repo_id.split("/", 1)[0] if "/" in repo_id else None,
        created_at=row.get("createdAt"),
        updated_at=row.get("lastModified"),
        tags=[t for t in tags if ":" not in t][:8],
        badges=badges,
        metrics=metrics,
    )


def _raise_for(data: Any, headers: dict[str, str]) -> None:
    if isinstance(data, dict) and data.get("error"):
        message = str(data["error"])
        if "429" in message:
            raise SourceUnavailable(
                "Hugging Face rate limit reached.",
                status="rate_limited",
                retry_after=rate_limit_reset(headers),
            )
        raise SourceUnavailable(message)


class HubSource:
    id = "hf"

    async def spec(self) -> SourceSpec:
        connected = bool(await huggingface.token())
        return SourceSpec(
            id=self.id,
            label="Hugging Face",
            requires_auth=False,
            connected=connected,
            auth_hint=None
            if connected
            else "Public repos only. Connect Hugging Face to include private and gated repos.",
            kinds=[
                KindSpec(
                    id="model",
                    label="Models",
                    sorts=_sorts("model"),
                    default_sort="trendingScore",
                    filters=[
                        FilterSpec(id="task", label="Task", options=_opts(_TASKS)),
                        FilterSpec(
                            id="library", label="Library", options=_opts(_LIBRARIES)
                        ),
                    ],
                    search_placeholder="Search models — name or owner…",
                ),
                KindSpec(
                    id="dataset",
                    label="Datasets",
                    sorts=_sorts("dataset"),
                    default_sort="trendingScore",
                    filters=[
                        FilterSpec(
                            id="modality", label="Modality", options=_opts(_MODALITIES)
                        )
                    ],
                    search_placeholder="Search datasets…",
                ),
                KindSpec(
                    id="space",
                    label="Spaces",
                    sorts=_sorts("space"),
                    default_sort="trendingScore",
                    filters=[FilterSpec(id="sdk", label="SDK", options=_opts(_SDKS))],
                    search_placeholder="Search Spaces…",
                ),
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        kind = query.kind if query.kind in _PATHS else "model"
        sort = query.sort if query.sort in _SORT_LABEL else "trendingScore"
        if kind == "space" and sort == "downloads":
            sort = "trendingScore"
        params: list[tuple[str, Any]] = [
            ("sort", sort),
            ("direction", -1),
            ("limit", PAGE_SIZE),
        ]
        params += [("expand[]", e) for e in _EXPAND[kind]]
        if query.q:
            params.append(("search", query.q))
        if task := query.filter("task"):
            params.append(("pipeline_tag", task))
        if library := query.filter("library"):
            params.append(("library", library))
        if modality := query.filter("modality"):
            params.append(("filter", f"modality:{modality}"))
        if sdk := query.filter("sdk"):
            params.append(("filter", sdk))
        if query.cursor:
            params.append(("cursor", query.cursor))

        data, headers = await huggingface_tools.hub_get(
            f"/{_PATHS[kind]}", params=params
        )
        _raise_for(data, headers)
        rows = data if isinstance(data, list) else []
        noun = {"model": "models", "dataset": "datasets", "space": "Spaces"}[kind]
        label = (
            f"{_SORT_LABEL[sort]} {noun} matching “{query.q}”"
            if query.q
            else f"{_SORT_LABEL[sort]} {noun} on the Hub"
        )
        return SourceResult(
            items=[to_item(r, kind) for r in rows if isinstance(r, dict)],
            feed_label=label,
            cursor_next=next_cursor(headers),
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        kind = kind if kind in _PATHS else "model"
        data, headers = await huggingface_tools.hub_get(f"/{_PATHS[kind]}/{item_id}")
        _raise_for(data, headers)
        info = data if isinstance(data, dict) else {}
        item = to_item(info, kind) if info.get("id") else known
        if item is None:
            raise SourceUnavailable(f"{item_id} not found on the Hub")
        if known is not None:
            # Keep the clicked row's figures. The single-repo endpoint omits
            # `downloadsAllTime`/`trendingScore`, and its `downloads` is served from a
            # different cache than the list's — they disagree by thousands on busy
            # repos — so mixing the two would put contradictory numbers side by side.
            item.metrics = known.metrics
            if not item.description:
                item.description = known.description

        facts: list[Fact] = []
        card = info.get("cardData") or {}
        params = (info.get("safetensors") or {}).get("total")
        if isinstance(params, (int, float)) and params > 0:
            facts.append(Fact(label="parameters", value=_human_params(params)))
        if lic := card.get("license"):
            facts.append(Fact(label="license", value=str(lic)))
        if model_type := (info.get("config") or {}).get("model_type"):
            facts.append(Fact(label="architecture", value=str(model_type)))
        for key, label in (
            ("task_categories", "task categories"),
            ("size_categories", "size category"),
            ("language", "language"),
            ("base_model", "base model"),
        ):
            value = card.get(key)
            if value:
                facts.append(
                    Fact(
                        label=label,
                        value=", ".join(map(str, value))
                        if isinstance(value, list)
                        else str(value),
                    )
                )
        if info.get("sha"):
            facts.append(Fact(label="revision", value=str(info["sha"])[:12]))

        files = [
            str(s.get("rfilename"))
            for s in (info.get("siblings") or [])
            if isinstance(s, dict) and s.get("rfilename")
        ]
        body: str | None = None
        if "README.md" in files or not files:
            readme = await huggingface_tools.read_text(item_id, "README.md", kind=kind)
            if isinstance(readme, dict) and readme.get("content"):
                body = strip_front_matter(str(readme["content"]))
        links = [Link(label="Open on the Hub", url=item.url or "")]
        if kind == "space":
            subdomain = (info.get("subdomain") or "").strip()
            if subdomain:
                links.append(
                    Link(label="Open the app", url=f"https://{subdomain}.hf.space")
                )
        return DiscoverDetail(
            item=item, body=body, facts=facts, links=links, files=files[:200]
        )


def _human_params(total: float) -> str:
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if total >= scale:
            return f"{total / scale:.1f}{unit} ({int(total):,})"
    return f"{int(total):,}"
