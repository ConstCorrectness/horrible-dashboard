"""REST surface for the social layer, mounted at `/api/social`.

Deliberately thin: every operation is a call into `roster`, which owns the state
machine and the peer-wire side. Note there is no endpoint that hands out a private
key — the person key never crosses this boundary, only the public half and the
friend code derived from it.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.modules.social import identity as person_identity
from backend.modules.social import handles, roster
from backend.modules.social.models import (
    AddFriendRequest,
    AddFriendResult,
    BindHandleResult,
    DirectorySearchResult,
    RespondRequest,
    RosterSnapshot,
    SelfProfile,
    UpdateProfileRequest,
)

router = APIRouter(prefix="/social", tags=["social"])


@router.get("/roster", response_model=RosterSnapshot)
async def get_roster() -> RosterSnapshot:
    return roster.snapshot()


@router.get("/me", response_model=SelfProfile)
async def get_me() -> SelfProfile:
    return roster.self_profile()


@router.post("/me", response_model=SelfProfile)
async def update_me(request: UpdateProfileRequest) -> SelfProfile:
    person_identity.save_profile(
        display_name=request.display_name, avatar=request.avatar
    )
    roster.broadcast_roster()
    return roster.self_profile()


@router.post("/friends", response_model=AddFriendResult)
async def add_friend(request: AddFriendRequest) -> AddFriendResult:
    friend, error = await roster.add_friend(request.code, request.address, request.note)
    return AddFriendResult(ok=error is None, friend=friend, error=error)


@router.post("/friends/respond", response_model=RosterSnapshot)
async def respond(request: RespondRequest) -> RosterSnapshot:
    await roster.respond(request.person_id, request.accept)
    return roster.snapshot()


@router.delete("/friends/{person_id}", response_model=RosterSnapshot)
async def remove_friend(person_id: str) -> RosterSnapshot:
    await roster.remove(person_id)
    return roster.snapshot()


@router.post("/friends/{person_id}/block", response_model=RosterSnapshot)
async def block_friend(person_id: str) -> RosterSnapshot:
    await roster.block(person_id)
    return roster.snapshot()


# ---- usernames (@handle) -----------------------------------------------------------
#
# The bridge between the two identities a person used to have: the game server's
# globally unique `handle` and the fabric's `person_id`. See social/handles.py.


@router.post("/handle/bind", response_model=BindHandleResult)
async def bind_handle() -> BindHandleResult:
    """Enroll this machine in the signed-in account now, rather than in the
    background — so a first-run screen can wait for `@username` to reach it.

    Idempotent; the same enrollment runs on every sign-in and startup.
    """
    from backend.modules.games import server_auth

    result = await handles.enroll_device()
    if result.get("error"):
        return BindHandleResult(error=str(result["error"]))
    return BindHandleResult(ok=True, handle=server_auth.signed_in_username())


@router.get("/directory/search", response_model=DirectorySearchResult)
async def search_directory(q: str) -> DirectorySearchResult:
    """Prefix-search usernames — the "easier way to find people".

    Entries whose `person_id` is not the fingerprint of their own published key are
    dropped in `handles.search`, so a compromised directory can withhold someone
    but never substitute a different key for them.
    """
    return DirectorySearchResult(results=await handles.search(q))  # type: ignore[arg-type]
