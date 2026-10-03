"""Cross-posting a page whole to dev.to and Hashnode.

The converter is tested on what it must not get wrong quietly: code passes through
untouched (a `$` in a shell line is not math), every site link becomes absolute, and
whatever has no equivalent is said rather than dropped. The two platforms are faked at
`social_http.transport`, so the connectors' real request code runs: dev.to's key goes in
`api-key`, Hashnode's token bare in `Authorization`, and a GraphQL error that arrives
with a 200 still fails the send.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.modules.connectors import store as connector_store
from backend.modules.connectors.providers import devto, hashnode, social_http
from backend.modules.connectors.store import Credential
from backend.modules.scrive import crosspost, outbox, publish, social, store
from backend.modules.scrive.runner import OutboxRunner

KEY = "devto-key-never-in-a-row"
PAT = "hashnode-pat-never-in-a-row"

PAGE = """---
title: Priors
description: Why priors matter.
tags: [Stats, machine-learning, myst, bayes, extra]
thumbnail: ../media/cover.png
status: published
---

(sec:intro)=
# Introduction

We model $y \\sim N(\\mu, 1)$ for $5 and $10 budgets. See {ref}`sec:intro`.

:::{note} On notation
Bold is a vector.
:::

```{dropdown} Derivation
$$
x = 1
$$
```

```bash
echo $HOME costs $5
```

```{figure} ../media/plot.png
:alt: A plot

The posterior.
```

Read [the other post](b.md) and [the docs](https://mystmd.org).

```{r3f} ../scenes/orbit.tsx
:height: 300
```

```{code-cell} python
print(1)
```

Done[^1].

[^1]: A footnote.
"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def client(tmp_path) -> TestClient:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    (data_dir / "settings.json").write_text(
        json.dumps(
            {"scrive.root": str(tmp_path / "scrive"), "scrive.semanticSearch": False}
        )
    )
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "blog"}).status_code == 200
    write("blog", "posts/a.md", PAGE)
    write("blog", "posts/b.md", "---\ntitle: B\nstatus: published\n---\n\nB.\n")
    write("blog", "media/plot.png", b"\x89PNG plot")
    write("blog", "media/cover.png", b"\x89PNG cover")
    return "blog"


@pytest.fixture(autouse=True)
def no_network():
    yield
    social_http.transport = None
    for cid in ("devto", "hashnode"):
        connector_store.clear(cid)


def write(site_id: str, rel: str, text: str | bytes) -> None:
    path = store.site_dir(site_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)


def published(site_id: str, files: list[str] | None = None) -> None:
    publish._write_record(
        site_id,
        publish.PublishRecord(
            mode="static",
            owner="alice",
            repo="blog",
            branch="main",
            url="https://alice.github.io/blog/",
            files=files
            if files is not None
            else [
                "index.html",
                "posts/a/index.html",
                "posts/b/index.html",
                "media/plot.png",
                "media/cover.png",
            ],
            published_at=time.time(),
        ),
    )


def connect_devto() -> None:
    connector_store.save(
        "devto", Credential(access_token=KEY, account={"id": "1", "label": "@me"})
    )


def connect_hashnode() -> None:
    connector_store.save(
        "hashnode",
        Credential(
            access_token=PAT,
            account={
                "id": "u1",
                "label": "@me · me.hashnode.dev",
                "publication_id": "pub-1",
                "publication_url": "https://me.hashnode.dev",
            },
        ),
    )


class Platforms:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.graphql: list[dict[str, Any]] = []
        social_http.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.host == "dev.to" and request.url.path == "/api/articles":
            return httpx.Response(
                201, json={"id": 77, "url": "https://dev.to/me/priors-1abc"}
            )
        if request.url.host == "dev.to" and request.url.path == "/api/users/me":
            if request.headers.get("api-key") != KEY:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(200, json={"id": 1, "username": "me"})
        if request.url.host == "gql.hashnode.com":
            body = json.loads(request.content)
            self.graphql.append(body)
            return self.answer_graphql(request, body)
        return httpx.Response(404, json={"detail": "unexpected"})

    def answer_graphql(
        self, request: httpx.Request, body: dict[str, Any]
    ) -> httpx.Response:
        if "publishPost" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publishPost": {
                            "post": {
                                "id": "p1",
                                "url": "https://me.hashnode.dev/priors",
                                "slug": "priors",
                            }
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "me": {
                        "id": "u1",
                        "username": "me",
                        "publications": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "pub-1",
                                        "title": "Mine",
                                        "url": "https://me.hashnode.dev",
                                    }
                                },
                                {
                                    "node": {
                                        "id": "pub-2",
                                        "title": "Work",
                                        "url": "https://blog.work.dev",
                                    }
                                },
                            ]
                        },
                    }
                }
            },
        )


# --- the converter ----------------------------------------------------------------------


def convert(flavor: crosspost.Flavor) -> crosspost.Converted:
    return crosspost.to_markdown(PAGE, page="posts/a.md", flavor=flavor)


def test_dev_to_math_details_and_embeds_are_liquid_tags() -> None:
    md = convert("devto").markdown
    assert "{% katex inline %}y \\sim N(\\mu, 1){% endkatex %}" in md
    assert (
        "{% details Derivation %}\n{% katex %}\nx = 1\n{% endkatex %}\n{% enddetails %}"
        in md
    )
    assert "> **On notation**\n>\n> Bold is a vector." in md


def test_hashnode_keeps_dollar_math_and_uses_html_details() -> None:
    md = convert("hashnode").markdown
    assert "$y \\sim N(\\mu, 1)$" in md
    assert "<details><summary>Derivation</summary>" in md and "$$\nx = 1\n$$" in md


def test_prices_and_code_are_not_math() -> None:
    md = convert("devto").markdown
    assert "for $5 and $10 budgets" in md
    assert "```bash\necho $HOME costs $5\n```" in md


def test_every_site_link_becomes_absolute() -> None:
    md = convert("devto").markdown
    assert "![A plot]({{site.url}}media/plot.png)\n\n*The posterior.*" in md
    assert "[the other post]({{site.url}}posts/b/)" in md
    assert "[the docs](https://mystmd.org)" in md
    assert "[Interactive 3D figure: open it on the site]({{post.url}})" in md


def test_headings_move_down_and_labels_read_as_their_heading() -> None:
    converted = convert("devto")
    assert "## Introduction" in converted.markdown
    assert "See Introduction." in converted.markdown
    assert "(sec:intro)=" not in converted.markdown
    assert "[^1]: A footnote." in converted.markdown


def test_what_does_not_carry_over_is_said() -> None:
    notes = " ".join(convert("devto").notes)
    assert "3D scenes" in notes and "outputs stay on the site" in notes
    assert crosspost.leftover_myst(convert("devto").markdown) == []
    assert crosspost.leftover_myst("```{tab-set}\n```\n{kbd}`x`") == ["tab-set", "kbd"]


# --- drafts, preflight and approval ------------------------------------------------------


def test_a_dev_to_draft_is_the_whole_page_with_its_canonical_url(site) -> None:
    payload = social.suggest(site, "posts/a.md", "devto")
    assert (
        payload["title"] == "Priors" and payload["description"] == "Why priors matter."
    )
    # Lowercase letters and digits only, at most four.
    assert payload["tags"] == ["stats", "machinelearning", "myst", "bayes"]
    assert payload["canonical_url"] == social.POST_URL
    assert payload["cover"] == "media/cover.png"
    assert payload["body"].startswith("<!-- Scrive: what changed from the page")
    assert "{{site.url}}media/plot.png" in payload["body"]


def test_approval_fills_both_urls_and_the_cover(client, site) -> None:
    connect_devto()
    published(site)
    item = outbox.create(site, "posts/a.md", "devto")
    res = client.post(f"/api/scrive/outbox/{item.id}/approve", json={}).json()
    assert res["approved"], res["item"]["findings"]
    payload = res["item"]["payload"]
    assert payload["canonical_url"] == "https://alice.github.io/blog/posts/a/"
    assert payload["cover"] == "https://alice.github.io/blog/media/cover.png"
    assert "https://alice.github.io/blog/media/plot.png" in payload["body"]
    assert "{{" not in json.dumps(payload)


def test_an_image_that_is_not_on_the_site_blocks_until_acknowledged(
    client, site
) -> None:
    connect_devto()
    published(
        site, files=["posts/a/index.html", "posts/b/index.html", "media/cover.png"]
    )
    item = outbox.create(site, "posts/a.md", "devto")
    findings = outbox.check(item.id)
    assert any(f.rule == "not-live" and f.file == "media/plot.png" for f in findings)
    approved, ok = outbox.approve(item.id)
    assert not ok
    approved, ok = outbox.approve(item.id, acknowledged=True)
    assert ok


def test_an_unpublished_site_and_a_fifth_tag_are_hard(site) -> None:
    connect_hashnode()
    item = outbox.create(
        site,
        "posts/a.md",
        "hashnode",
        {
            "title": "T",
            "body": "![x]({{site.url}}media/plot.png)",
            "tags": list("abcdef"),
        },
    )
    rules = {f.rule for f in outbox.check(item.id)}
    assert {"unpublished", "tags"} <= rules
    assert not outbox.approve(item.id, acknowledged=True)[1]


def test_the_agent_drafts_a_cross_post(site) -> None:
    from backend.modules.scrive.agent_tools import register_agent_tools
    from backend.sdk.registry import registry

    register_agent_tools()
    out = run(
        registry.agent_tools["scrive.draftSocial"].handler(
            {"site": site, "page": "posts/a.md", "target": "hashnode"}
        )
    )
    item = outbox.get(out["id"])
    assert item.status == "draft" and item.created_by == "agent"
    assert item.payload["subtitle"] == "Why priors matter."


# --- sending -----------------------------------------------------------------------------


def approved(site: str, target: str) -> outbox.OutboxItem:
    published(site)
    item = outbox.create(site, "posts/a.md", target)
    item, ok = outbox.approve(item.id, acknowledged=True)
    assert ok, item.findings
    return outbox.send(item.id)


def test_dev_to_gets_the_article_with_the_key_in_its_own_header(site) -> None:
    connect_devto()
    platforms = Platforms()
    item = approved(site, "devto")
    sent = run(OutboxRunner().run(item.id))
    assert sent.status == "sent" and sent.url == "https://dev.to/me/priors-1abc"
    assert sent.remote_id == "77"
    request = platforms.calls[-1]
    assert request.headers["api-key"] == KEY and "authorization" not in request.headers
    article = json.loads(request.content)["article"]
    assert article["canonical_url"] == "https://alice.github.io/blog/posts/a/"
    assert article["main_image"] == "https://alice.github.io/blog/media/cover.png"
    assert article["published"] is True and len(article["tags"]) == 4
    assert KEY not in json.dumps(outbox.get(item.id).model_dump())


def test_hashnode_publishes_to_the_chosen_publication_with_a_bare_token(site) -> None:
    connect_hashnode()
    platforms = Platforms()
    item = approved(site, "hashnode")
    sent = run(OutboxRunner().run(item.id))
    assert sent.status == "sent" and sent.url == "https://me.hashnode.dev/priors"
    assert platforms.calls[-1].headers["authorization"] == PAT
    post = platforms.graphql[-1]["variables"]["input"]
    assert post["publicationId"] == "pub-1"
    assert post["originalArticleURL"] == "https://alice.github.io/blog/posts/a/"
    assert {"slug": "machine-learning", "name": "machine-learning"} in post["tags"]
    assert post["coverImageOptions"]["coverImageURL"].endswith("media/cover.png")


def test_a_graphql_error_with_a_200_fails_the_send(site, monkeypatch) -> None:
    connect_hashnode()
    platforms = Platforms()
    monkeypatch.setattr(
        platforms,
        "answer_graphql",
        lambda request, body: httpx.Response(
            200, json={"errors": [{"message": "Tag limit exceeded"}], "data": None}
        ),
    )
    item = approved(site, "hashnode")
    failed = run(OutboxRunner().run(item.id))
    assert failed.status == "failed" and "Tag limit exceeded" in failed.error
    # An answer, not a lost request: the post step is not "unknown".
    assert failed.steps["post"]["status"] == "pending"


def test_a_refused_hashnode_token_says_reconnect() -> None:
    social_http.transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "errors": [
                    {"message": "nope", "extensions": {"code": "UNAUTHENTICATED"}}
                ]
            },
        )
    )
    with pytest.raises(social_http.SendError) as err:
        run(hashnode.graphql("query { me { id } }", {}, PAT))
    assert err.value.status == 401 and "reconnect" in str(err.value)


# --- connecting --------------------------------------------------------------------------


def test_connecting_hashnode_needs_a_publication_when_there_are_several() -> None:
    Platforms()
    out = run(hashnode._submit({"api_key": PAT}))
    assert "several publications" in out["error"] and "blog.work.dev" in out["error"]
    out = run(hashnode._submit({"api_key": PAT, "host": "https://blog.work.dev/"}))
    assert out["connected"]
    assert hashnode.publication() == {
        "id": "pub-2",
        "title": "Work",
        "url": "https://blog.work.dev",
    }


def test_a_dev_to_key_is_checked_before_it_is_kept() -> None:
    Platforms()
    assert "error" in run(devto._submit({"api_key": "wrong"}))
    assert not connector_store.is_connected("devto")
    assert run(devto._submit({"api_key": KEY}))["account"]["label"] == "@me"
    assert connector_store.is_connected("devto")
