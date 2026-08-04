"""Rich profiles: artwork, status, showcases, comment walls, and image uploads.

Two classes of thing are pinned here.

**The feature that did not exist.** A profile could only be read by its owner —
there was no endpoint for anyone else's — which is why the Plaza's player card
rendered a hardcoded placeholder bio for every player. `GET /profile/{handle}` is
the fix, and `test_another_players_profile_is_readable` is the regression.

**The upload gate**, which is the part with teeth. An image endpoint on a server
with a volume is an arbitrary-write primitive if any of these slip:

- a declared `Content-Type` believed over the actual bytes,
- SVG treated as an image (it executes script),
- a `sha` from the URL concatenated into a path,
- an unbounded read before the size check.

Each has a test, and each would be silent in normal use.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.games_server import store

#: A real 8-byte PNG signature plus filler — enough for the magic-number sniffer.
PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 200
GIF = b"GIF89a" + b"\x00" * 32


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("HORRIBLE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAMES_ALLOW_DEV_AUTH", "1")  # bearer == account id
    from backend.games_server.app import app

    store.init_db()
    with store.get_conn() as conn:
        for account_id, handle in (("acc1", "rob"), ("acc2", "ann"), ("acc3", "cee")):
            conn.execute(
                "INSERT INTO accounts (id, provider, subject, display_name, created_at, handle)"
                " VALUES (?, 'test', ?, ?, ?, ?)",
                (account_id, account_id, account_id.upper(), time.time(), handle),
            )
    return TestClient(app)


def auth(account_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {account_id}"}


# ---- reading someone else's profile ------------------------------------------------


def test_another_players_profile_is_readable(client: TestClient) -> None:
    """The regression. Before this endpoint the Plaza card showed everyone the same
    invented bio, because there was no way to fetch a profile you did not own."""
    client.post("/profile", headers=auth("acc2"), json={"bio": "ann's actual bio"})
    profile = client.get("/profile/ann").json()["profile"]
    assert profile["bio"] == "ann's actual bio"
    assert profile["handle"] == "ann"


def test_reading_a_profile_needs_no_sign_in(client: TestClient) -> None:
    """A profile is as public as the ladder that shows their rating."""
    assert "profile" in client.get("/profile/rob").json()


def test_unknown_handle_is_not_an_empty_profile(client: TestClient) -> None:
    """ "No such player" and "a player who has written nothing" are different."""
    assert client.get("/profile/nobody").json() == {"error": "no such player"}


def test_profile_patch_leaves_unspecified_fields_alone(client: TestClient) -> None:
    client.post("/profile", headers=auth("acc1"), json={"bio": "kept"})
    out = client.post(
        "/profile", headers=auth("acc1"), json={"status_text": "afk"}
    ).json()["profile"]
    assert out["bio"] == "kept" and out["status_text"] == "afk"


@pytest.mark.parametrize(
    ("field", "cap"),
    [("bio", store.BIO_MAX), ("status_text", store.STATUS_MAX)],
)
def test_text_fields_are_capped(client: TestClient, field: str, cap: int) -> None:
    out = client.post("/profile", headers=auth("acc1"), json={field: "y" * (cap + 500)})
    assert len(out.json()["profile"][field]) == cap


def test_avatar_column_still_holds_only_an_emoji(client: TestClient) -> None:
    """The 8-char cap is correct and stays. What was broken was the *frontend*
    writing a base64 image into this column; images belong in `avatar_url`."""
    out = client.post("/profile", headers=auth("acc1"), json={"avatar": "x" * 50})
    assert len(out.json()["profile"]["avatar"]) == store.AVATAR_EMOJI_MAX


# ---- uploads: the gate -------------------------------------------------------------


def test_upload_round_trips_and_is_content_addressed(client: TestClient) -> None:
    body = client.post(
        "/profile/media?kind=avatar",
        headers={**auth("acc1"), "Content-Type": "image/png"},
        content=PNG,
    ).json()
    assert body["url"] == f"/media/{body['sha256']}"

    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    # Immutable: the URL is the hash, so it can never mean different bytes.
    assert "immutable" in served.headers["cache-control"]

    # The same image twice is the same address, stored once.
    again = client.post(
        "/profile/media?kind=avatar",
        headers={**auth("acc1"), "Content-Type": "image/png"},
        content=PNG,
    ).json()
    assert again["sha256"] == body["sha256"]


def test_declared_type_is_checked_against_the_bytes(client: TestClient) -> None:
    """An allowlist keyed on what the caller *says* is not an allowlist."""
    out = client.post(
        "/profile/media?kind=avatar",
        headers={**auth("acc1"), "Content-Type": "image/png"},
        content=GIF,
    ).json()
    assert "does not match" in out["error"]


def test_svg_is_refused(client: TestClient) -> None:
    """SVG is a script-execution surface wearing an image's clothes."""
    out = client.post(
        "/profile/media?kind=avatar",
        headers={**auth("acc1"), "Content-Type": "image/svg+xml"},
        content=b"<svg onload='alert(1)'/>",
    ).json()
    assert "unsupported" in out["error"]


def test_oversize_is_refused(client: TestClient) -> None:
    out = client.post(
        "/profile/media?kind=avatar",
        headers={**auth("acc1"), "Content-Type": "image/png"},
        content=b"\x89PNG\r\n\x1a\n" + b"\x00" * (store.MEDIA_MAX_BYTES + 10),
    ).json()
    assert "larger than" in out["error"]


@pytest.mark.parametrize(
    "sha",
    ["..%2f..%2fetc%2fpasswd", "../../secret", "zzzz", "", "a" * 63, "A" * 64],
)
def test_media_ids_that_are_not_hex_are_not_paths(client: TestClient, sha: str) -> None:
    """`sha` lands in a filesystem path, so anything but 64 lowercase hex is a 404
    before it gets near the disk. Uppercase too: it would be a second name for the
    same blob on a case-insensitive filesystem."""
    assert client.get(f"/media/{sha}").status_code == 404


def test_upload_needs_sign_in(client: TestClient) -> None:
    out = client.post(
        "/profile/media?kind=avatar", headers={"Content-Type": "image/png"}, content=PNG
    ).json()
    assert out["error"] == "sign in required"


def test_upload_kind_is_constrained(client: TestClient) -> None:
    out = client.post(
        "/profile/media?kind=../../etc",
        headers={**auth("acc1"), "Content-Type": "image/png"},
        content=PNG,
    ).json()
    assert "kind must be" in out["error"]


def test_per_account_media_is_capped(client: TestClient) -> None:
    """A quota, so one account cannot fill the volume one avatar at a time."""
    for i in range(store.MEDIA_PER_ACCOUNT + 5):
        client.post(
            "/profile/media?kind=avatar",
            headers={**auth("acc1"), "Content-Type": "image/png"},
            content=b"\x89PNG\r\n\x1a\n" + bytes([i % 251]) * 64,
        )
    with store.get_conn() as conn:
        kept = conn.execute(
            "SELECT COUNT(*) AS n FROM profile_media WHERE account_id = 'acc1'"
        ).fetchone()["n"]
    assert kept <= store.MEDIA_PER_ACCOUNT


# ---- comment walls -----------------------------------------------------------------


def test_comment_round_trips_with_its_author(client: TestClient) -> None:
    posted = client.post(
        "/profile/rob/comments", headers=auth("acc2"), json={"body": "gg wp"}
    ).json()["comment"]
    assert posted["body"] == "gg wp"

    wall = client.get("/profile/rob/comments").json()["comments"]
    # The author is joined live, so a comment shows who they *are*, not the name
    # they had when they wrote it.
    assert [(c["author_handle"], c["body"]) for c in wall] == [("ann", "gg wp")]


def test_a_comment_outlives_its_author_being_offline(client: TestClient) -> None:
    """The reason walls live on the server rather than riding the peer fabric."""
    client.post("/profile/rob/comments", headers=auth("acc2"), json={"body": "later"})
    # No session, no socket, nobody connected — the wall still reads.
    assert client.get("/profile/rob/comments").json()["comments"][0]["body"] == "later"


def test_anonymous_comments_are_refused(client: TestClient) -> None:
    out = client.post("/profile/rob/comments", json={"body": "spam"}).json()
    assert out["error"] == "sign in required"


def test_empty_comments_are_refused(client: TestClient) -> None:
    out = client.post(
        "/profile/rob/comments", headers=auth("acc2"), json={"body": "   "}
    ).json()
    assert out["error"] == "comment was empty"


def test_comments_on_an_unknown_wall_are_refused(client: TestClient) -> None:
    out = client.post(
        "/profile/nobody/comments", headers=auth("acc2"), json={"body": "hi"}
    ).json()
    assert out["error"] == "no such player"


def test_only_the_owner_or_author_may_hide(client: TestClient) -> None:
    cid = client.post(
        "/profile/rob/comments", headers=auth("acc2"), json={"body": "x"}
    ).json()["comment"]["id"]

    assert client.delete(f"/profile/comments/{cid}", headers=auth("acc3")).json() == {
        "error": "not yours to remove"
    }
    assert client.delete(f"/profile/comments/{cid}", headers=auth("acc1")).json() == {
        "ok": True
    }
    assert client.get("/profile/rob/comments").json()["comments"] == []


def test_an_author_may_retract_their_own(client: TestClient) -> None:
    cid = client.post(
        "/profile/rob/comments", headers=auth("acc2"), json={"body": "oops"}
    ).json()["comment"]["id"]
    assert client.delete(f"/profile/comments/{cid}", headers=auth("acc2")).json() == {
        "ok": True
    }


def test_hidden_comments_are_kept_not_destroyed(client: TestClient) -> None:
    """A wall owner moderating their page must not be able to erase the record of
    what was said to them."""
    cid = client.post(
        "/profile/rob/comments", headers=auth("acc2"), json={"body": "evidence"}
    ).json()["comment"]["id"]
    client.delete(f"/profile/comments/{cid}", headers=auth("acc1"))
    with store.get_conn() as conn:
        row = conn.execute(
            "SELECT body, hidden FROM profile_comments WHERE id = ?", (cid,)
        ).fetchone()
    assert row["body"] == "evidence" and row["hidden"] == 1


def test_comment_body_is_capped(client: TestClient) -> None:
    posted = client.post(
        "/profile/rob/comments",
        headers=auth("acc2"),
        json={"body": "z" * (store.COMMENT_MAX + 500)},
    ).json()["comment"]
    assert len(posted["body"]) == store.COMMENT_MAX


# ---- batched cards ------------------------------------------------------------------
#
# What a *list* needs, in one request. The alternative is one profile fetch per
# friend on every render of a pane that opens by default.


def test_cards_return_face_and_level_for_many_at_once(client: TestClient) -> None:
    client.post("/profile", headers=auth("acc1"), json={"avatar": "🦊", "status_text": "afk"})
    body = client.post("/profiles/cards", json={"handles": ["rob", "ann"]}).json()
    cards = body["cards"]
    assert set(cards) == {"rob", "ann"}
    assert cards["rob"]["avatar"] == "🦊"
    assert cards["rob"]["status_text"] == "afk"
    assert cards["rob"]["level"] >= 1


def test_a_card_for_someone_who_never_opened_the_plaza(client: TestClient) -> None:
    """A LEFT JOIN miss is a real row with null profile columns, not a null row.
    Read carelessly it crashes on `level_for_xp(None)`; this is the common case."""
    cards = client.post("/profiles/cards", json={"handles": ["cee"]}).json()["cards"]
    assert cards["cee"]["level"] == 1
    assert cards["cee"]["xp"] == 0
    assert cards["cee"]["avatar"]


def test_unknown_handles_are_absent_not_errors(client: TestClient) -> None:
    cards = client.post("/profiles/cards", json={"handles": ["rob", "nobody"]}).json()["cards"]
    assert set(cards) == {"rob"}


def test_cards_are_capped(client: TestClient) -> None:
    """Batched, but never a way to walk the player base."""
    asked = [f"h{i}" for i in range(store.MAX_PROFILE_CARDS + 50)] + ["rob"]
    cards = client.post("/profiles/cards", json={"handles": asked}).json()["cards"]
    # "rob" fell off the end of the cap, so nothing matched — the cap is a slice of
    # the request, not a filter applied after the query.
    assert cards == {}


def test_cards_of_nothing(client: TestClient) -> None:
    assert client.post("/profiles/cards", json={"handles": []}).json() == {"cards": {}}
