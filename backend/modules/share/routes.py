"""HTTP surface for the `share` module, mounted at `/api/share`.

The `/ws` `share` channel is the live path; these routes are the pull half — what
a pane asks for on mount, and what the agent tools call. Both go through the same
`share_manager`, so there is one authority and no second copy of the state.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.modules.share import fabric, link, session as share_session
from backend.modules.share.models import (
    ActionResult,
    GrantIn,
    InviteIn,
    Invitee,
    JoinIn,
    LinkOut,
    LinkStatusOut,
    MintLinkIn,
    RestreamIn,
    RestreamOut,
    SessionOut,
    ShareSession,
    StartSessionIn,
)
from backend.modules.share.session import share_manager

router = APIRouter(prefix="/share", tags=["share"])


@router.get("", response_model=SessionOut)
async def get_state() -> SessionOut:
    """Everything this node knows about sharing: what it hosts, what it has
    joined, and what it has been invited to."""
    return share_manager.snapshot()


@router.post("/session", response_model=ShareSession)
async def start_session(body: StartSessionIn) -> ShareSession:
    return await share_manager.start(body.title, body.mode)


@router.delete("/session", response_model=ActionResult)
async def stop_session() -> ActionResult:
    await share_manager.stop()
    return ActionResult(ok=True)


@router.get("/invitees", response_model=list[Invitee])
async def get_invitees() -> list[Invitee]:
    """Friends who could join right now.

    The roster join happens backend-side so the pane never imports across a
    module boundary — it only ever calls `/api/share`.
    """
    return await share_session.list_invitees()


@router.post("/invite", response_model=ActionResult)
async def invite(body: InviteIn) -> ActionResult:
    hosting = share_manager.hosting
    if hosting is None:
        return ActionResult(ok=False, error="no session is running")
    sent = await fabric.invite_person(body.person_id, hosting.id, hosting.title)
    # Zero reached is not a failure: an invite to an offline friend is queued and
    # flushed when their machine comes back. Say which happened rather than
    # reporting a success that looks like a delivery.
    return ActionResult(ok=True, detail={"delivered": sent, "queued": sent == 0})


@router.post("/grant", response_model=ActionResult)
async def grant(body: GrantIn) -> ActionResult:
    ok = await share_manager.set_grant(body.person_id, body.grant)
    return ActionResult(ok=ok, error=None if ok else "not a guest in this session")


@router.post("/revoke-all", response_model=ActionResult)
async def revoke_all() -> ActionResult:
    await share_manager.revoke_all()
    return ActionResult(ok=True)


@router.post("/join", response_model=ActionResult)
async def join(body: JoinIn) -> ActionResult:
    ok, error = await fabric.join_remote(body.session_id, body.host_node)
    return ActionResult(ok=ok, error=error)


@router.post("/leave", response_model=ActionResult)
async def leave(body: JoinIn) -> ActionResult:
    await fabric.leave_remote(body.session_id)
    return ActionResult(ok=True)


@router.get("/link", response_model=LinkOut)
async def get_link() -> LinkOut:
    """The live link, for the host's own browser.

    Exists because the host's tab can reload mid-session and still needs the
    ingest URL to resume publishing — and because that URL must not ride the
    broadcast that guests receive.
    """
    session = share_manager.hosting
    return LinkOut(
        view_url=session.link if session else "",
        ingest_url=share_manager.link_ingest,
        expires_at=share_manager.link_expires_at,
    )


@router.post("/link", response_model=LinkOut)
async def mint_link(body: MintLinkIn) -> LinkOut:
    """Mint a public link. Reports a relay failure as text, not a 500.

    A misconfigured or unreachable relay is an ordinary situation with an
    actionable cause ("no relay configured", "the relay rejected this node's
    key"), and a stack trace in the log helps the host not at all.
    """
    try:
        await share_manager.mint_link(ttl_s=body.ttl_s, passphrase=body.passphrase)
    except link.LinkError as exc:
        return LinkOut(error=str(exc))
    session = share_manager.hosting
    return LinkOut(
        view_url=session.link if session else "",
        ingest_url=share_manager.link_ingest,
        expires_at=share_manager.link_expires_at,
    )


@router.delete("/link", response_model=ActionResult)
async def revoke_link() -> ActionResult:
    """Kill the public link immediately. The session and its guests carry on."""
    await share_manager.revoke_link()
    return ActionResult(ok=True)


@router.get("/link/status", response_model=LinkStatusOut)
async def link_status() -> LinkStatusOut:
    """Whether the relay still has the live link.

    Polled by the host's pane while it is streaming, because nothing else can
    notice a relay-side death: the relay's registry lives in one process's
    memory, so a crash or a redeploy silently drops every token while the host's
    peer connection sits there believing it is still publishing.

    Answers `unknown` rather than a guess whenever the relay could not be asked —
    see `LinkStatusOut`.
    """
    if not share_manager.link_token:
        return LinkStatusOut(state="unknown", detail="No public link is minted.")
    status = await link.stream_status(share_manager.link_token)
    return LinkStatusOut(
        state=str(status.get("state") or "unknown"),
        live=bool(status.get("live")),
        viewers=int(status.get("viewers") or 0),
        expires_at=float(status.get("expires_at") or 0.0),
        detail=str(status.get("detail") or ""),
    )


@router.get("/restream", response_model=RestreamOut)
async def get_restream() -> RestreamOut:
    """Whether a restream is running, and which destinations could be used.

    `available` comes from the connector and lists destination **ids only** — the
    keys never leave `secrets.db`, and this response goes to the browser.
    """
    from backend.modules.share import streaming

    status = await link.restream_status(share_manager.link_token)
    return RestreamOut(
        live=bool(status.get("live")),
        label=str(status.get("label") or ""),
        available=streaming.configured_destinations(),
        error=str(status.get("error") or ""),
    )


@router.post("/restream", response_model=RestreamOut)
async def start_restream(body: RestreamIn) -> RestreamOut:
    """Start pushing the public stream to Twitch/YouTube/an RTMP server.

    Reports a misconfiguration as text rather than a 500: "no key stored for
    twitch" is an ordinary state with an obvious fix, and a stack trace helps the
    host not at all.
    """
    from backend.modules.share import streaming

    try:
        label = await link.start_restream(share_manager.link_token, body.destination)
    except link.LinkError as exc:
        return RestreamOut(
            available=streaming.configured_destinations(), error=str(exc)
        )
    return RestreamOut(
        live=True, label=label, available=streaming.configured_destinations()
    )


@router.delete("/restream", response_model=ActionResult)
async def stop_restream() -> ActionResult:
    await link.stop_restream(share_manager.link_token)
    return ActionResult(ok=True)
