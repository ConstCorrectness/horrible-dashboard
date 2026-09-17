"""Trajectories across the fabric: serving, pulling, and live watching.

A loopback hub stands in for the network: `request` hands the envelope to the
handler the module actually registered and returns whatever that handler replied,
so the request/reply path, the trust gate and the paging all run for real. It also
enforces the one wire limit this module is designed around — every reply must fit
inside the 1 MiB frame a dialing node's `websockets` client will accept — because a
reply that does not fit does not raise anywhere: it closes the friend's whole link.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.modules.network.models import PeerEnvelope
from backend.modules.trajectories import fabric, store
from backend.modules.trajectories.models import StepWrite

FRIEND = "FRIENDNODE234567"
WS_CLIENT_MAX = 1024 * 1024


class Info:
    def __init__(self, node_id: str, trusted: bool = True) -> None:
        self.node_id = node_id
        self.node_name = f"name-{node_id}"
        self.trusted = trusted


class Session:
    def __init__(self, node_id: str = FRIEND, trusted: bool = True) -> None:
        self.info = Info(node_id, trusted)


class LoopbackHub:
    """Routes a request straight to the serving handler and back."""

    def __init__(self, trusted: bool = True) -> None:
        self.handlers: dict[str, Any] = {}
        self.sent: list[tuple[str, str, dict[str, Any], str | None]] = []
        self.trusted = trusted
        self.reply_sizes: list[int] = []
        self._replies: dict[str, PeerEnvelope] = {}
        self._n = 0
        fabric.register(self)

    def register_handler(
        self, msg_type: str, handler: Any, mode: str = "inline"
    ) -> None:
        self.handlers[msg_type] = handler

    async def send_to(
        self, node_id: str, msg_type: str, data: dict[str, Any], re: str | None = None
    ) -> None:
        self.sent.append((node_id, msg_type, data, re))
        if re is not None:
            size = len(json.dumps(data, default=str))
            self.reply_sizes.append(size)
            assert size < WS_CLIENT_MAX, f"reply of {size} bytes would close the link"
            self._replies[re] = PeerEnvelope(
                type=msg_type, msg_id=f"r{re}", re=re, src=FRIEND, ts=0.0, data=data
            )

    async def request(
        self, node_id: str, msg_type: str, data: dict[str, Any], timeout: float = 0
    ) -> PeerEnvelope:
        self._n += 1
        msg_id = f"m{self._n}"
        env = PeerEnvelope(type=msg_type, msg_id=msg_id, src=node_id, ts=0.0, data=data)
        await self.handlers[msg_type](self, Session(node_id, self.trusted), env)
        if msg_id not in self._replies:
            raise TimeoutError("no reply")  # what silence looks like to a requester
        return self._replies.pop(msg_id)

    def list_peers(self) -> list[Any]:
        return []


@pytest.fixture()
def hub(monkeypatch):
    store._initialized.clear()
    store.init_trajectories_db()
    loop = LoopbackHub()
    import backend.modules.network.hub as hub_mod

    monkeypatch.setattr(hub_mod, "peer_hub", loop)
    fabric._watchable.clear()
    return loop


def _shared_run(
    steps: list[StepWrite], *, goal: str = "do it", status: str = "complete"
) -> str:
    if store.get_dataset("mine") is None:
        store.create_dataset("mine", "Mine")
        store.update_dataset("mine", shared=True)
    run_id = store.start_run(
        "mine", goal=goal, turn_id="private-turn", external_id="ext-1"
    )
    for step in steps:
        store.append_step(run_id, step)
    if status != "running":
        store.finish_run(run_id, status=status)
    return run_id


# ------------------------------------------------------------------------ gates


@pytest.mark.anyio
async def test_an_unshared_dataset_is_invisible(hub):
    store.create_dataset("private", "Private")
    run_id = store.start_run("private", goal="secret plans")
    store.finish_run(run_id)

    assert await fabric.list_peer_datasets(FRIEND) == []
    with pytest.raises(fabric.PeerRefused, match="not shared"):
        await fabric.fetch_peer_run(FRIEND, run_id)


@pytest.mark.anyio
async def test_a_missing_run_and_an_unshared_one_get_the_same_answer(hub):
    """The reply must not confirm what exists."""
    store.create_dataset("private", "Private")
    hidden = store.start_run("private")
    with pytest.raises(fabric.PeerRefused) as unshared:
        await fabric.fetch_peer_run(FRIEND, hidden)
    with pytest.raises(fabric.PeerRefused) as missing:
        await fabric.fetch_peer_run(FRIEND, "no-such-run")
    assert str(unshared.value) == str(missing.value)


@pytest.mark.anyio
async def test_an_untrusted_peer_gets_silence(hub):
    hub.trusted = False
    _shared_run([StepWrite(kind="action", name="t")])
    with pytest.raises(fabric.PeerRefused, match="did not answer"):
        await fabric.list_peer_datasets(FRIEND)
    assert hub.sent == []


def test_a_peer_dataset_cannot_be_shared_onward(hub):
    store.create_dataset("peer-x", "From a friend", source_kind="peer")
    with pytest.raises(store.SharingRefused):
        store.update_dataset("peer-x", shared=True)


@pytest.mark.anyio
async def test_a_peer_dataset_is_not_served_even_if_the_flag_is_forced(hub):
    """The refusal is enforced where data is served, not only where the flag is set —
    a row edited directly in `app.db` must not become a relay for a friend's runs."""
    store.create_dataset("peer-x", "From a friend", source_kind="peer")
    run_id = store.start_run("peer-x", source="peer")
    store.finish_run(run_id)
    with store.get_db_conn() as conn:
        conn.execute("UPDATE traj_datasets SET shared = 1 WHERE id = 'peer-x'")

    assert await fabric.list_peer_datasets(FRIEND) == []
    with pytest.raises(fabric.PeerRefused):
        await fabric.fetch_peer_run(FRIEND, run_id)


# ---------------------------------------------------------------- what is sent


@pytest.mark.anyio
async def test_private_joins_and_identities_never_leave(hub):
    run_id = _shared_run([StepWrite(kind="action", name="t")])
    runs = await fabric.list_peer_runs(FRIEND, "mine")
    fetched = await fabric.fetch_peer_run(FRIEND, run_id)
    for header in (runs[0], fetched["run"]):
        for private in (
            "turn_id",
            "external_id",
            "person_id",
            "node_id",
            "meta",
            "parent_run_id",
        ):
            assert private not in header


@pytest.mark.anyio
async def test_credentials_are_redacted_before_they_leave(hub):
    run_id = _shared_run(
        [
            StepWrite(
                kind="action",
                name="http.get",
                args={
                    "url": "https://api.example",
                    "headers": {"Authorization": "Bearer abc123def456ghi"},
                },
                result={
                    "body": "token ghp_0123456789abcdefghijABCDEFGHIJ issued",
                    "api_key": "k-1",
                },
            ),
            StepWrite(
                kind="message",
                role="assistant",
                content="the password=hunter2222 worked",
            ),
        ],
        goal="rotate key sk-proj-0123456789abcdefABCDEF",
    )
    blob = json.dumps(await fabric.fetch_peer_run(FRIEND, run_id))
    for secret in (
        "abc123def456ghi",
        "ghp_0123456789",
        "k-1",
        "hunter2222",
        "sk-proj-0123",
    ):
        assert secret not in blob
    # Every sent frame, not only the assembled result.
    assert all("hunter2222" not in json.dumps(d) for _, _, d, _ in hub.sent)


# -------------------------------------------------------------------- paging


@pytest.mark.anyio
async def test_a_large_run_is_paged_under_the_frame_limit(hub):
    """Twelve 100 KB results — over a megabyte in all. Sent unpaged, the one reply
    would close the dialing friend's link; paged, it arrives whole and in order."""
    steps = [
        StepWrite(
            kind="action", name=f"read{i}", result={"text": f"{i}:" + "x" * 100_000}
        )
        for i in range(12)
    ]
    run_id = _shared_run(steps)

    fetched = await fabric.fetch_peer_run(FRIEND, run_id)

    assert len(hub.reply_sizes) > 1
    assert max(hub.reply_sizes) < WS_CLIENT_MAX
    assert [s["seq"] for s in fetched["steps"]] == list(range(12))
    assert all(
        s["result"]["text"].startswith(f"{i}:") for i, s in enumerate(fetched["steps"])
    )


@pytest.mark.anyio
async def test_a_spilled_blob_is_sent_as_its_content_not_its_pointer(hub):
    """Above 16 KB a payload lives on disk and the column holds `blob:<name>` — a path
    on *this* machine. Sent as-is it would arrive as a run missing its largest results."""
    big = {"text": "y" * (store.STEP_PAYLOAD_MAX * 2)}
    run_id = _shared_run([StepWrite(kind="action", name="t", result=big)])
    with store.get_db_conn() as conn:
        raw = conn.execute(
            "SELECT result FROM traj_steps WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    assert raw.startswith(store.BLOB_PREFIX)

    fetched = await fabric.fetch_peer_run(FRIEND, run_id)
    assert fetched["steps"][0]["result"] == big


@pytest.mark.anyio
async def test_an_oversized_payload_arrives_as_a_marker(hub):
    run_id = _shared_run(
        [StepWrite(kind="action", name="t", result={"t": "z" * 300_000})]
    )
    fetched = await fabric.fetch_peer_run(FRIEND, run_id)
    assert fetched["steps"][0]["result"]["_omitted"]
    assert fetched["steps"][0]["result"]["bytes"] > 300_000


@pytest.mark.anyio
async def test_a_peer_that_pages_backwards_is_refused(hub, monkeypatch):
    """`next_seq` is untrusted input; a loop that followed it blindly could run forever."""
    run_id = _shared_run(
        [StepWrite(kind="action", name="a"), StepWrite(kind="action", name="b")]
    )

    def looping(run: str, from_seq: int) -> dict[str, Any]:
        page = {"steps": [{"seq": 0, "kind": "action"}], "next_seq": 0}
        if from_seq <= 0:
            page["run"] = {"id": run, "status": "complete"}
        return page

    monkeypatch.setattr(fabric, "build_page", looping)
    with pytest.raises(fabric.PeerRefused, match="out of order"):
        await fabric.fetch_peer_run(FRIEND, run_id)


# ---------------------------------------------------------------------- pull


@pytest.mark.anyio
async def test_a_pulled_run_is_stored_as_a_peer_run_with_fabric_provenance(hub):
    from backend.modules.trajectories.models import LabelWrite

    run_id = _shared_run(
        [StepWrite(kind="action", name="t", result={"ok": True})], goal="summarize"
    )
    store.add_label(run_id, LabelWrite(key="outcome", value="success"))

    local_id = await fabric.pull_run(FRIEND, run_id)

    pulled = store.get_run(local_id)
    dataset = store.get_dataset(pulled.dataset_id)
    assert pulled.source == "peer"
    assert pulled.id != run_id
    assert dataset.id == fabric.peer_dataset_id(FRIEND)
    assert dataset.source_kind == "peer"
    # Identity comes from the authenticated node, not from anything in the payload.
    assert pulled.node_id == FRIEND
    assert pulled.goal == "summarize"
    # A friend's grade arrives as an import, never as this node's human judgment.
    assert [(lbl.key, lbl.source) for lbl in pulled.labels] == [("outcome", "import")]


@pytest.mark.anyio
async def test_re_pulling_replaces_rather_than_duplicates(hub):
    from backend.modules.trajectories.models import LabelWrite

    run_id = _shared_run(
        [StepWrite(kind="action", name="a"), StepWrite(kind="action", name="b")]
    )
    store.add_label(run_id, LabelWrite(key="outcome", value="success"))

    first = await fabric.pull_run(FRIEND, run_id)
    second = await fabric.pull_run(FRIEND, run_id)

    assert first == second
    pulled = store.get_run(second)
    assert pulled.steps == 2
    assert len(pulled.labels) == 1


@pytest.mark.anyio
async def test_a_local_grade_on_a_pulled_run_survives_a_re_pull(hub):
    from backend.modules.trajectories.models import LabelWrite

    run_id = _shared_run([StepWrite(kind="action", name="a")])
    local_id = await fabric.pull_run(FRIEND, run_id)
    store.add_label(
        local_id, LabelWrite(key="outcome", value="failure", source="human")
    )

    await fabric.pull_run(FRIEND, run_id)
    assert [lbl.source for lbl in store.get_run(local_id).labels] == ["human"]


@pytest.mark.anyio
async def test_a_running_run_cannot_be_pulled(hub):
    run_id = _shared_run([StepWrite(kind="action", name="a")], status="running")
    with pytest.raises(fabric.PeerRefused, match="still in progress"):
        await fabric.pull_run(FRIEND, run_id)


@pytest.mark.anyio
async def test_a_malformed_run_leaves_nothing_behind(hub, monkeypatch):
    """Validated in full before anything is written — including the dataset."""
    run_id = _shared_run([StepWrite(kind="action", name="a")])

    def bad(run: str, from_seq: int) -> dict[str, Any]:
        return {
            "run": {"id": run, "status": "complete"},
            "steps": [{"seq": 0, "kind": "not-a-kind"}],
            "next_seq": None,
        }

    monkeypatch.setattr(fabric, "build_page", bad)
    with pytest.raises(fabric.PeerRefused, match="cannot read"):
        await fabric.pull_run(FRIEND, run_id)
    assert store.get_dataset(fabric.peer_dataset_id(FRIEND)) is None


# ---------------------------------------------------------------------- live


class _Participant:
    def __init__(self, node_id: str, grant: str = "view", role: str = "guest") -> None:
        self.node_id = node_id
        self.grant = grant
        self.role = role


def _hosting(monkeypatch, participants: list[_Participant] | None) -> None:
    from backend.modules.share.session import share_manager

    hosting = (
        None
        if participants is None
        else type("H", (), {"participants": participants})()
    )
    monkeypatch.setattr(share_manager, "hosting", hosting)


@pytest.mark.anyio
async def test_live_events_reach_session_participants_only(hub, monkeypatch):
    run_id = _shared_run([], status="running")
    _hosting(
        monkeypatch, [_Participant("host-self", role="host"), _Participant("GUEST1")]
    )

    await fabric.forward_live(
        "run", store.get_run(run_id, with_steps=False).model_dump()
    )
    await fabric.forward_live(
        "step",
        {
            "runId": run_id,
            "step": {
                "seq": 0,
                "kind": "action",
                "name": "t",
                "content": "password=hunter2222",
            },
        },
    )

    live = [(node, d) for node, t, d, _ in hub.sent if t == fabric.TRAJ_LIVE]
    assert {node for node, _ in live} == {"GUEST1"}
    assert [d["event"] for _, d in live] == ["run", "step"]
    assert "hunter2222" not in json.dumps(live)


@pytest.mark.anyio
async def test_no_session_means_nothing_is_streamed(hub, monkeypatch):
    run_id = _shared_run([], status="running")
    _hosting(monkeypatch, None)
    await fabric.forward_live(
        "run", store.get_run(run_id, with_steps=False).model_dump()
    )
    assert not [t for _, t, _, _ in hub.sent if t == fabric.TRAJ_LIVE]


@pytest.mark.anyio
async def test_an_unshared_run_is_not_streamed_even_inside_a_session(hub, monkeypatch):
    store.create_dataset("private", "Private")
    run_id = store.start_run("private")
    _hosting(monkeypatch, [_Participant("GUEST1")])
    await fabric.forward_live(
        "run", store.get_run(run_id, with_steps=False).model_dump()
    )
    await fabric.forward_live(
        "step", {"runId": run_id, "step": {"seq": 0, "kind": "action"}}
    )
    assert not [t for _, t, _, _ in hub.sent if t == fabric.TRAJ_LIVE]


@pytest.mark.anyio
async def test_the_grant_ladder_decides_not_this_module(hub, monkeypatch):
    """A participant whose grant resolves below `view` is not streamed to. The rung
    comparison is `share.gate`'s — the point is that this module asks it."""
    from backend.modules.share import gate

    run_id = _shared_run([], status="running")
    _hosting(monkeypatch, [_Participant("GUEST1")])
    monkeypatch.setattr(gate, "require", lambda p, needed: (False, "no"))
    await fabric.forward_live(
        "run", store.get_run(run_id, with_steps=False).model_dump()
    )
    assert not [t for _, t, _, _ in hub.sent if t == fabric.TRAJ_LIVE]


@pytest.mark.anyio
async def test_a_guest_only_accepts_live_runs_from_a_host_it_joined(hub, monkeypatch):
    import backend.modules.ws as ws_mod
    from backend.modules.share.models import RemoteSession
    from backend.modules.share.session import share_manager

    shown: list[dict[str, Any]] = []

    async def capture(channel, event, data):
        shown.append(data)

    monkeypatch.setattr(ws_mod, "broadcast_event", capture)
    monkeypatch.setattr(share_manager, "joined", {})
    envelope = PeerEnvelope(
        type=fabric.TRAJ_LIVE,
        msg_id="x",
        src="HOSTNODE",
        ts=0.0,
        data={"event": "run", "data": {"id": "r1"}},
    )

    # A trusted friend who has not invited us into a session: dropped.
    await fabric.handle_live(hub, Session("HOSTNODE"), envelope)
    assert shown == []

    # Once joined, frames carry share's own name for the host (resolved from the
    # roster at join time — see `test_share_session`), tagged with the node id.
    share_manager.joined["s1"] = RemoteSession(
        id="s1",
        title="t",
        host_node="HOSTNODE",
        host_name="Ada",
        joined_at=0.0,
    )
    await fabric.handle_live(hub, Session("HOSTNODE"), envelope)
    assert shown and shown[0]["host"] == "HOSTNODE"
    assert shown[0]["hostName"] == "Ada"

    # And never from an untrusted node, joined or not.
    shown.clear()
    await fabric.handle_live(hub, Session("HOSTNODE", trusted=False), envelope)
    assert shown == []


# --------------------------------------------------------------------- routes


class _PeerInfo:
    def __init__(self, node_id: str, trusted: bool) -> None:
        self.node_id = node_id
        self.trusted = trusted
        self.capabilities = ["trajectories"]


def test_routes_refuse_to_relay_to_a_node_that_is_not_a_trusted_friend(
    hub, monkeypatch
):
    """Without this check this node's routes would forward requests to any node it is
    merely connected to — a stranger on the LAN included."""
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setattr(
        hub, "list_peers", lambda: [_PeerInfo("STRANGER", trusted=False)]
    )
    client = TestClient(app)
    for path in (
        "/api/trajectories/peers/STRANGER/datasets",
        "/api/trajectories/peers/NOBODY/datasets",
    ):
        assert client.get(path).status_code == 404
    assert (
        client.post("/api/trajectories/peers/STRANGER/runs/r1/pull").status_code == 404
    )


def test_a_friends_refusal_is_a_502_with_its_reason(hub, monkeypatch):
    from fastapi.testclient import TestClient

    from backend.app import app

    monkeypatch.setattr(hub, "list_peers", lambda: [_PeerInfo(FRIEND, trusted=True)])
    response = TestClient(app).get(f"/api/trajectories/peers/{FRIEND}/runs/nope")
    assert response.status_code == 502
    assert "not shared" in response.json()["detail"]


def test_sharing_a_peer_dataset_is_a_409_on_the_route(hub):
    from fastapi.testclient import TestClient

    from backend.app import app

    store.create_dataset("peer-y", "From a friend", source_kind="peer")
    response = TestClient(app).patch(
        "/api/trajectories/datasets/peer-y", json={"shared": True}
    )
    assert response.status_code == 409
    assert store.get_dataset("peer-y").shared is False
