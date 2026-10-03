"""Scrive's outbox and its three targets.

Every platform is faked at one seam — `social_http.transport`, an `httpx.MockTransport`
— so the connectors' and senders' real request code runs. The rules under test are the
quiet ones: the agent can only draft; approval runs preflight, freezes the payload and
fills in the link; nothing hard can be acknowledged away; a scheduled row goes out
when the ticker reaches it; a send resumes from its checkpoints and never re-posts a
post it already made; a post whose outcome is unknown fails instead of guessing; and
no token ever lands in a row.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.modules.connectors import oauth
from backend.modules.connectors import store as connector_store
from backend.modules.connectors.providers import linkedin, social_http, x, youtube
from backend.modules.connectors.store import Credential
from backend.modules.scrive import outbox, publish, senders, social, store
from backend.modules.scrive.runner import OutboxRunner

FAKE_TOKEN = "ghp_" + "c" * 36
ACCESS = "access-token-never-stored-in-a-row"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def scrive_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "scrive"
    (data_dir / "settings.json").write_text(
        json.dumps({"scrive.root": str(root), "scrive.semanticSearch": False})
    )
    return root


@pytest.fixture
def client(scrive_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "blog"}).status_code == 200
    write(
        "blog",
        "posts/a.md",
        "---\ntitle: Priors\ndescription: Why priors matter.\n"
        "tags: [stats]\nstatus: published\n---\n\n## One\n\nText.\n\n## Two\n\nMore.\n",
    )
    return "blog"


@pytest.fixture(autouse=True)
def no_network():
    yield
    social_http.transport = None
    oauth.reset_flows()
    for cid in ("x", "linkedin", "youtube"):
        connector_store.clear(cid)


def write(site_id: str, rel: str, text: str | bytes) -> None:
    path = store.site_dir(site_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)


def connect(*ids: str, expires_in: float = 3600) -> None:
    for cid in ids:
        connector_store.save(
            cid,
            Credential(
                access_token=ACCESS,
                refresh_token="refresh-1",
                expires_at=time.time() + expires_in,
                account={"id": "42", "label": "me", "username": "me"},
            ),
        )


def published(site_id: str, page: str = "posts/a.md") -> None:
    """A static publish record listing the page, as `publish.publish` writes it."""
    rec = publish.PublishRecord(
        mode="static",
        owner="alice",
        repo="blog",
        branch="main",
        url="https://alice.github.io/blog/",
        files=[f"{social.page_dir(page)}index.html", "index.html"],
        published_at=time.time(),
    )
    publish._write_record(site_id, rec)


class Platform:
    """A fake of the three APIs. Records each request; answers by route."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.next_post = 100
        self.fail: dict[str, list[Any]] = {}
        self.youtube_have = 0
        social_http.transport = httpx.MockTransport(self.handle)

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.calls if r.url.path == path]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path in self.fail and self.fail[path]:
            outcome = self.fail[path].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome[0], json=outcome[1])
        if path == "/2/tweets":
            self.next_post += 1
            return httpx.Response(201, json={"data": {"id": str(self.next_post)}})
        if path == "/2/media/upload/initialize":
            return httpx.Response(200, json={"data": {"id": "m1"}})
        if path.endswith("/append"):
            return httpx.Response(204)
        if path.endswith("/finalize"):
            return httpx.Response(200, json={"data": {"id": "m1"}})
        if path == "/rest/images":
            return httpx.Response(
                200,
                json={
                    "value": {
                        "uploadUrl": "https://upload.example/img",
                        "image": "urn:li:image:9",
                    }
                },
            )
        if request.url.host == "upload.example":
            return httpx.Response(201)
        if path == "/rest/posts":
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:7"})
        if path == "/upload/youtube/v3/videos":
            return httpx.Response(
                200, headers={"location": "https://session.example/session"}
            )
        if request.url.host == "session.example" or path == "/session":
            rng = request.headers.get("content-range", "")
            total = int(rng.rsplit("/", 1)[1])
            if rng.startswith("bytes */"):
                have = self.youtube_have
            else:
                start, end = rng.split(" ", 1)[1].split("/")[0].split("-")
                assert int(start) == self.youtube_have, (
                    "a chunk resent from the wrong offset"
                )
                have = self.youtube_have = int(end) + 1
            if have >= total:
                return httpx.Response(
                    200, json={"id": "vid1", "status": {"privacyStatus": "private"}}
                )
            return httpx.Response(308, headers={"range": f"bytes=0-{have - 1}"})
        if path == "/2/oauth2/token":
            form = parse_qs(request.content.decode())
            return httpx.Response(
                200,
                json={
                    "access_token": "new-access",
                    "refresh_token": "refresh-2",
                    "expires_in": 7200,
                    "scope": "tweet.write",
                    "echo": form,
                },
            )
        return httpx.Response(
            404, json={"detail": f"unexpected {request.method} {path}"}
        )


# --- drafts and approval ----------------------------------------------------------------


def test_the_agent_can_only_draft(client, site) -> None:
    from backend.modules.scrive.agent_tools import register_agent_tools
    from backend.sdk.registry import registry

    register_agent_tools()
    out = run(
        registry.agent_tools["scrive.draftSocial"].handler(
            {"site": site, "page": "posts/a.md", "target": "x", "note": "short thread"}
        )
    )
    assert out["status"] == "draft", out
    item = outbox.get(out["id"])
    assert item.created_by == "agent" and item.note == "short thread"
    assert "Priors" in item.payload["posts"][0]["text"]
    # Nothing in the group can move it on.
    tools = [n for n in registry.agent_tools if n.startswith("scrive.")]
    assert not any(
        w in n.lower() for n in tools for w in ("approve", "send", "schedule")
    )


def test_a_suggested_youtube_draft_lists_the_headings_as_chapters(site) -> None:
    payload = social.suggest(site, "posts/a.md", "youtube")
    assert "0:00 One\n0:00 Two" in payload["description"]
    assert payload["tags"] == ["stats"] and payload["privacy"] == "private"


def test_approval_freezes_the_payload_with_the_live_link(client, site) -> None:
    connect("linkedin")
    published(site)
    item = outbox.create(site, "posts/a.md", "linkedin")
    assert item.payload["link"] == social.POST_URL
    res = client.post(f"/api/scrive/outbox/{item.id}/approve", json={}).json()
    assert res["approved"], res
    assert res["item"]["payload"]["link"] == "https://alice.github.io/blog/posts/a/"
    assert res["item"]["status"] == "approved"
    # An edit takes the approval back.
    edited = client.put(
        f"/api/scrive/outbox/{item.id}", json={"payload": res["item"]["payload"]}
    ).json()
    assert edited["status"] == "draft"


def test_a_link_to_an_unpublished_page_cannot_be_approved(client, site) -> None:
    connect("linkedin")
    item = outbox.create(site, "posts/a.md", "linkedin")
    res = client.post(
        f"/api/scrive/outbox/{item.id}/approve", json={"acknowledged": True}
    ).json()
    assert not res["approved"]
    assert "unpublished" in {f["rule"] for f in res["item"]["findings"]}


def test_a_secret_blocks_until_acknowledged_but_hard_rules_never_give(
    client, site
) -> None:
    connect("x")
    item = outbox.create(
        site, "posts/a.md", "x", {"posts": [{"text": f"key {FAKE_TOKEN}"}], "link": ""}
    )
    first = client.post(f"/api/scrive/outbox/{item.id}/approve", json={}).json()
    assert not first["approved"]
    assert any(f["severity"] == "secret" for f in first["item"]["findings"])
    assert FAKE_TOKEN not in json.dumps(first["item"]["findings"])
    second = client.post(
        f"/api/scrive/outbox/{item.id}/approve", json={"acknowledged": True}
    ).json()
    assert second["approved"]

    long = outbox.create(
        site, "posts/a.md", "x", {"posts": [{"text": "a" * 281}], "link": ""}
    )
    res = client.post(
        f"/api/scrive/outbox/{long.id}/approve", json={"acknowledged": True}
    )
    assert not res.json()["approved"]
    assert "too-long" in {f["rule"] for f in res.json()["item"]["findings"]}


def test_a_target_that_is_not_connected_cannot_be_approved(site) -> None:
    item = outbox.create(
        site, "posts/a.md", "x", {"posts": [{"text": "hi"}], "link": ""}
    )
    _, approved = outbox.approve(item.id, acknowledged=True)
    assert not approved
    assert "not-connected" in {f.rule for f in outbox.get(item.id).findings}


def test_send_and_schedule_need_approval(client, site) -> None:
    item = outbox.create(
        site, "posts/a.md", "x", {"posts": [{"text": "hi"}], "link": ""}
    )
    assert client.post(f"/api/scrive/outbox/{item.id}/send").status_code == 409
    assert (
        client.post(
            f"/api/scrive/outbox/{item.id}/schedule",
            json={"run_at": time.time() + 3600},
        ).status_code
        == 409
    )


# --- X rules ---------------------------------------------------------------------------


def test_x_weighted_length() -> None:
    assert social.x_length("a" * 280) == 280
    assert social.x_length("日本語") == 6
    assert (
        social.x_length("go https://example.com/a/very/long/path?q=1 now") == 3 + 23 + 4
    )
    assert social.x_length("👍🏽") == 2  # the skin-tone modifier rides on the emoji
    assert social.x_length(social.POST_URL) == 23


def test_x_link_goes_in_a_reply_and_the_cost_counts_it(site) -> None:
    payload = social.XPayload(
        posts=[social.XPost(text="one"), social.XPost(text="two")]
    )
    thread = social.x_thread(payload)
    assert [p.text for p in thread] == ["one", "two", social.POST_URL]
    assert social.x_cost(payload) == (0.23, 3, 1)
    inline = payload.model_copy(update={"link_in_reply": False})
    assert social.x_thread(inline)[0].text == f"one\n\n{social.POST_URL}"


def test_x_media_rules(site) -> None:
    connect("x")
    write(site, "media/a.png", b"png")
    write(site, "media/v.mp4", b"mp4")
    findings = social.preflight(
        site,
        "posts/a.md",
        "x",
        {
            "posts": [
                {
                    "text": "hi",
                    "media": ["media/a.png", "media/v.mp4", "media/gone.png"],
                }
            ],
            "link": "",
        },
    )
    rules = [f.rule for f in findings]
    assert "missing-file" in rules and "media" in rules and "cost" in rules


# --- the ticker and the runner -------------------------------------------------------------


def approved_x(site: str, posts: list[str], link: str = "") -> outbox.OutboxItem:
    connect("x")
    item = outbox.create(
        site, "posts/a.md", "x", {"posts": [{"text": t} for t in posts], "link": link}
    )
    item, ok = outbox.approve(item.id)
    assert ok, item.findings
    return item


def test_the_ticker_sends_a_scheduled_post_when_its_time_comes(site) -> None:
    platform = Platform()
    item = approved_x(site, ["hello"])
    outbox.schedule(item.id, time.time() + 3600)
    runner = OutboxRunner()

    async def go():
        assert await runner.tick() == []  # not yet
        assert await runner.tick(now=time.time() + 3601) == [item.id]
        return await runner.run(item.id)

    done = run(go())
    assert done.status == "sent" and done.remote_id == "101"
    assert done.url == "https://x.com/me/status/101"
    assert platform.bodies("/2/tweets") == [{"text": "hello"}]


def test_a_thread_replies_to_itself(site) -> None:
    platform = Platform()
    item = approved_x(site, ["one", "two"], link="https://example.com/p")
    outbox.send(item.id)
    run(OutboxRunner().run(item.id))
    bodies = platform.bodies("/2/tweets")
    assert [b["text"] for b in bodies] == ["one", "two", "https://example.com/p"]
    assert [b.get("reply", {}).get("in_reply_to_tweet_id") for b in bodies] == [
        None,
        "101",
        "102",
    ]


def test_a_rate_limit_backs_off_then_sends(site) -> None:
    platform = Platform()
    platform.fail["/2/tweets"] = [(429, {"detail": "Too Many Requests"})]
    item = approved_x(site, ["hi"])
    outbox.send(item.id)
    runner = OutboxRunner()
    waiting = run(runner.run(item.id))
    assert (
        waiting.status == "sending"
        and waiting.attempts == 1
        and waiting.next_attempt_at
    )
    assert waiting.steps["post-0"]["status"] == "pending"
    assert run(runner.run(item.id)).status == "sending"  # still backing off
    assert run(runner.tick(now=waiting.next_attempt_at + 1)) == []
    outbox._update(item.id, next_attempt_at=time.time() - 1)
    done = run(runner.run(item.id))
    assert done.status == "sent"


def test_a_refusal_fails_with_the_platforms_reason(site) -> None:
    platform = Platform()
    platform.fail["/2/tweets"] = [
        (
            403,
            {"detail": "You are not allowed to create a Tweet with duplicate content."},
        )
    ]
    item = approved_x(site, ["hi"])
    outbox.send(item.id)
    failed = run(OutboxRunner().run(item.id))
    assert failed.status == "failed" and "duplicate content" in failed.error


def test_a_thread_cut_off_resumes_without_reposting(site) -> None:
    platform = Platform()
    platform.fail["/2/tweets"] = [
        (201, {"data": {"id": "500"}}),
        (503, {"detail": "busy"}),
    ]
    item = approved_x(site, ["one", "two"])
    outbox.send(item.id)
    runner = OutboxRunner()
    first = run(runner.run(item.id))
    assert first.status == "sending" and first.steps["post-0"] == {
        "kind": "post",
        "status": "done",
        "id": "500",
    }
    outbox._update(item.id, next_attempt_at=None)
    done = run(runner.run(item.id))
    assert done.status == "sent" and done.remote_id == "500"
    texts = [b["text"] for b in platform.bodies("/2/tweets")]
    assert texts == [
        "one",
        "two",
        "two",
    ]  # "one" posted once; "two" retried after the 503
    assert platform.bodies("/2/tweets")[-1]["reply"] == {"in_reply_to_tweet_id": "500"}


def test_a_post_with_no_answer_fails_instead_of_guessing(site) -> None:
    platform = Platform()
    platform.fail["/2/tweets"] = [httpx.ReadTimeout("timed out")]
    item = approved_x(site, ["hi"])
    outbox.send(item.id)
    failed = run(OutboxRunner().run(item.id))
    assert failed.status == "failed" and "may or may not have gone out" in failed.error
    assert failed.steps["post-0"]["status"] == "unknown"
    # Retry is the person accepting the risk: it posts again.
    outbox.retry(item.id)
    assert run(OutboxRunner().run(item.id)).status == "sent"
    assert len(platform.bodies("/2/tweets")) == 2


def test_a_restart_mid_post_marks_it_unknown(site) -> None:
    Platform()
    item = approved_x(site, ["hi"])
    outbox.send(item.id)
    outbox.save_step(item.id, "post-0", "running", kind="post")
    outbox.save_step(item.id, "media-0-0", "running", media_id="m1", segment=2)
    assert outbox.mark_unknown_on_boot() == 2
    steps = outbox.get(item.id).steps
    assert steps["post-0"]["status"] == "unknown"
    assert steps["media-0-0"] == {"media_id": "m1", "segment": 2, "status": "pending"}
    assert run(OutboxRunner().run(item.id)).status == "failed"


def test_x_media_upload_resumes_at_its_saved_segment(site, monkeypatch) -> None:
    platform = Platform()
    monkeypatch.setattr(senders, "X_CHUNK", 4)
    write(site, "media/clip.mp4", b"0123456789")  # three segments of 4
    connect("x")
    item = outbox.create(
        site,
        "posts/a.md",
        "x",
        {"posts": [{"text": "v", "media": ["media/clip.mp4"]}], "link": ""},
    )
    item, ok = outbox.approve(item.id)
    assert ok, item.findings
    outbox.send(item.id)
    # A previous run got the media id and the first two segments in, then stopped.
    outbox.save_step(item.id, "media-0-0", "uploading", media_id="m1", segment=2)
    done = run(OutboxRunner().run(item.id))
    assert done.status == "sent"
    appends = [r for r in platform.calls if r.url.path.endswith("/append")]
    assert len(appends) == 1 and b'name="segment_index"\r\n\r\n2' in appends[0].content
    assert not [r for r in platform.calls if r.url.path.endswith("/initialize")]
    assert platform.bodies("/2/tweets")[0]["media"] == {"media_ids": ["m1"]}


def test_youtube_resumes_from_the_offset_youtube_confirms(site, monkeypatch) -> None:
    platform = Platform()
    monkeypatch.setattr(senders, "YOUTUBE_CHUNK", 4)
    write(site, "media/talk.mp4", b"abcdefghij")
    connect("youtube")
    item = outbox.create(
        site,
        "posts/a.md",
        "youtube",
        {"video": "media/talk.mp4", "title": "Talk", "description": ""},
    )
    item, ok = outbox.approve(item.id)
    assert ok, item.findings
    outbox.send(item.id)
    outbox.save_step(
        item.id,
        "upload",
        "uploading",
        session="https://session.example/session",
        offset=4,
    )
    platform.youtube_have = 8  # YouTube got more than our checkpoint says
    done = run(OutboxRunner().run(item.id))
    assert done.status == "sent" and done.url == "https://www.youtube.com/watch?v=vid1"
    puts = [r.headers["content-range"] for r in platform.calls if r.method == "PUT"]
    assert puts == ["bytes */10", "bytes 8-9/10"]
    assert not platform.bodies("/upload/youtube/v3/videos")


def test_youtube_metadata_and_the_private_lock(site) -> None:
    connect("youtube")
    write(site, "media/talk.mp4", b"x")
    model = social.YouTubePayload(
        video="media/talk.mp4",
        title="T",
        privacy="public",
        publish_at="2099-01-01T00:00:00Z",
        synthetic=True,
    )
    meta = social.youtube_metadata(model)
    assert meta["status"] == {
        "privacyStatus": "private",
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": True,
        "publishAt": "2099-01-01T00:00:00Z",
    }
    rules = {
        f.rule
        for f in social.preflight(site, "posts/a.md", "youtube", model.model_dump())
    }
    assert "private-lock" in rules
    bad = social.preflight(
        site,
        "posts/a.md",
        "youtube",
        {"video": "media/talk.mp4", "title": "a <b>", "description": "0:00 a\n0:05 b"},
    )
    assert {"bad-text", "chapters"} <= {f.rule for f in bad}


def test_linkedin_sends_an_escaped_link_card_with_its_image(site) -> None:
    platform = Platform()
    connect("linkedin", expires_in=40 * 86400)
    published(site)
    write(site, "media/card.png", b"png")
    item = outbox.create(
        site,
        "posts/a.md",
        "linkedin",
        {
            "text": "Priors (part 1) #stats",
            "title": "Priors",
            "thumbnail": "media/card.png",
        },
    )
    item, ok = outbox.approve(item.id)
    assert ok, item.findings
    outbox.send(item.id)
    done = run(OutboxRunner().run(item.id))
    assert (
        done.status == "sent"
        and done.url == "https://www.linkedin.com/feed/update/urn:li:share:7/"
    )
    (body,) = platform.bodies("/rest/posts")
    assert body["commentary"] == r"Priors \(part 1\) \#stats"
    assert body["author"] == "urn:li:person:42"
    assert body["content"]["article"] == {
        "source": "https://alice.github.io/blog/posts/a/",
        "title": "Priors",
        "thumbnail": "urn:li:image:9",
    }
    post = next(r for r in platform.calls if r.url.path == "/rest/posts")
    assert post.headers["linkedin-version"] == linkedin.DEFAULT_VERSION


def test_no_token_ever_lands_in_a_row(site) -> None:
    Platform()
    item = approved_x(site, ["hi"])
    outbox.send(item.id)
    run(OutboxRunner().run(item.id))
    assert ACCESS not in outbox.get(item.id).model_dump_json()


# --- the connectors ----------------------------------------------------------------------


def test_x_authorize_url_is_pkce_with_the_exact_redirect(monkeypatch) -> None:
    monkeypatch.setenv("X_CLIENT_ID", "client-x")
    step = run(x._begin({}))
    query = parse_qs(urlsplit(step["authorize_url"]).query)
    assert query["code_challenge_method"] == ["S256"] and query["code_challenge"][0]
    assert query["redirect_uri"] == [oauth.redirect_uri("x")]
    assert "offline.access" in query["scope"][0] and "media.write" in query["scope"][0]


def test_x_without_a_client_id_asks_for_one_and_names_the_callback(monkeypatch) -> None:
    monkeypatch.delenv("X_CLIENT_ID", raising=False)
    step = run(x._begin({}))
    assert step["step"] == "form"
    assert oauth.redirect_uri("x") in step["fields"][0]["help"]


def test_x_refresh_keeps_the_rotated_refresh_token(monkeypatch) -> None:
    monkeypatch.setenv("X_CLIENT_ID", "client-x")
    Platform()
    connect("x", expires_in=10)  # inside the refresh window
    assert run(x.token()) == "new-access"
    assert connector_store.load("x").refresh_token == "refresh-2"


def test_linkedin_status_counts_down_to_expiry() -> None:
    connect("linkedin", expires_in=40 * 86400)
    cred = connector_store.load("linkedin")
    cred.refresh_token = None
    connector_store.save("linkedin", cred)
    status = linkedin._status()
    assert "expires in 40 days" in status.account.label and status.error is None
    cred.expires_at = time.time() + 3 * 86400
    connector_store.save("linkedin", cred)
    assert "3 days" in linkedin._status().error
    cred.expires_at = time.time() - 1
    connector_store.save("linkedin", cred)
    assert "expired" in linkedin._status().error
    assert run(linkedin.token()) is None


def test_youtube_uses_the_google_client_but_its_own_grant(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gsecret")
    step = run(youtube._begin({}))
    query = parse_qs(urlsplit(step["authorize_url"]).query)
    assert query["client_id"] == ["gid"]
    assert youtube.UPLOAD_SCOPE in query["scope"][0]
    assert query["redirect_uri"] == [oauth.redirect_uri("youtube")]
    assert not connector_store.is_connected("google")


def test_connector_status_never_carries_a_token(client) -> None:
    connect("x", "linkedin", "youtube")
    body = client.get("/api/connectors").text
    assert ACCESS not in body and "refresh-1" not in body
    tiles = {c["id"]: c for c in client.get("/api/connectors").json()["connectors"]}
    assert {"x", "linkedin", "youtube"} <= set(tiles)
    assert all(tiles[c]["connected"] for c in ("x", "linkedin", "youtube"))
