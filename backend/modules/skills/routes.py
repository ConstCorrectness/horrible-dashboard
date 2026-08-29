"""HTTP surface for skills. Mounted at `/api/skills`."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.modules.skills import agent, store
from backend.modules.skills.models import (
    EnableInput,
    ExportResponse,
    PreviewResponse,
    SkillCostResponse,
    SkillFileContent,
    SkillInput,
    SkillListResponse,
    SkillModel,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])


def _model(skill: store.Skill) -> SkillModel:
    return SkillModel(**skill.public(), enabled=store.is_enabled(skill.name))


@router.get("", response_model=SkillListResponse)
def list_skills() -> SkillListResponse:
    """Every discovered skill, from both sources, including the broken ones."""
    return SkillListResponse(
        skills=[_model(s) for s in store.list_skills()],
        userDir=str(store.user_dir()),
        projectDir=str(store.project_dir()),
    )


def _require(name: str) -> store.Skill:
    skill = store.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"no skill '{name}'")
    return skill


@router.post("", response_model=SkillModel)
def create_or_update(payload: SkillInput) -> SkillModel:
    """Write a user skill. Editing a project skill is refused — copy it first."""
    existing = store.get(payload.name)
    if existing is not None and existing.scope == "project" and not existing.shadowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{payload.name}' is a project skill in {store.project_dir()}. "
                "Copy it to your own skills to edit it."
            ),
        )
    skill = store.Skill(
        name=payload.name.strip(),
        description=payload.description.strip(),
        body=payload.body,
        allowed_tools=[t.strip() for t in payload.allowedTools if t.strip()],
        scope="user",
        # Anything the file already carried that this app has no opinion about is
        # preserved, so a round trip through the editor doesn't strip a field a newer
        # format (or Claude Code) put there.
        extra=existing.extra if existing and existing.scope == "user" else {},
    )
    saved, err = store.save(skill)
    if err or saved is None:
        raise HTTPException(status_code=400, detail=err or "could not save the skill")
    # After the write, never before: dropping the cache first leaves a window in which
    # a concurrent turn refills it from the pre-write state, and the agent then runs
    # for two seconds on the version the user just replaced.
    agent.invalidate()
    return _model(saved)


@router.delete("/{name}", response_model=SkillListResponse)
def delete_skill(name: str) -> SkillListResponse:
    """Delete a user skill. A project skill belongs to the repo and is never removed."""
    skill = _require(name)
    if skill.scope != "user":
        raise HTTPException(
            status_code=409,
            detail="that skill belongs to the project; delete it in the repository.",
        )
    if not store.delete(name):
        raise HTTPException(status_code=404, detail=f"no skill '{name}'")
    agent.invalidate()
    return list_skills()


@router.post("/{name}/enabled", response_model=SkillModel)
def set_enabled(name: str, payload: EnableInput) -> SkillModel:
    """Switch a skill's catalog line on or off.

    Disabling is the lever that matters: a disabled skill costs nothing per turn, and
    the honest response to "my context is full" is switching off the skills that never
    fire, not rewriting their descriptions.
    """
    skill = _require(name)
    store.set_enabled(skill.name, payload.enabled)
    agent.invalidate()
    return _model(skill)


@router.get("/cost", response_model=SkillCostResponse)
async def cost() -> SkillCostResponse:
    """What the catalog costs every turn, per skill and in total."""
    from backend.modules.agent.orchestrator import _tokenizer_repo

    return SkillCostResponse(**await agent.cost_async(_tokenizer_repo()))


@router.get("/{name}/preview", response_model=PreviewResponse)
def preview(name: str) -> PreviewResponse:
    """The two things the model actually receives, assembled.

    Not a rendering of the markdown — the literal strings, because the question this
    answers is "what did I just make the agent read", and a prettified preview would
    hide exactly the leading whitespace and stray frontmatter that cause trouble.
    """
    skill = _require(name)
    return PreviewResponse(
        catalog=agent.catalog_text() or "",
        instructions=skill.body,
        groups=agent.groups_for(skill),
    )


@router.get("/{name}/files/{rel:path}", response_model=SkillFileContent)
def read_file(name: str, rel: str) -> SkillFileContent:
    """One file from a skill's directory, as text.

    Read-only, and the containment check lives in `store.read_file` rather than here —
    see its docstring. This route's only job is turning that function's error string
    into a status code.
    """
    _require(name)
    text, err = store.read_file(name, rel)
    if err or text is None:
        raise HTTPException(status_code=404, detail=err or f"no file '{rel}'")
    return SkillFileContent(name=rel, bytes=len(text.encode("utf-8")), text=text)


@router.post("/{name}/copy", response_model=SkillModel)
def copy_to_user(name: str) -> SkillModel:
    """Copy a project skill into the user's own directory, resources and all."""
    copied, err = store.copy_to_user(name)
    if err or copied is None:
        raise HTTPException(status_code=400, detail=err or "could not copy the skill")
    agent.invalidate()
    return _model(copied)


@router.post("/{name}/export", response_model=ExportResponse)
def export(name: str) -> ExportResponse:
    """Copy a user skill into `.claude/skills/` so Claude Code picks it up unchanged.

    A copy, not a symlink — see `store.export_to_project`. The two diverge after an
    edit, which is the price of working on Windows without elevation.
    """
    path, err = store.export_to_project(name)
    if err or path is None:
        raise HTTPException(status_code=400, detail=err or "could not export the skill")
    return ExportResponse(path=path)
