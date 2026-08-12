"""Pydantic models for the skills API boundary.

`body` is carried in the list response on purpose, unlike most catalogs here. The pane
has to show what the model will actually receive, and a second fetch per skill to
render a preview would make the editor feel like a remote filesystem. Skill bodies are
kilobytes, not megabytes, and the list is local.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Scope = Literal["user", "project"]


class SkillModel(BaseModel):
    name: str
    description: str = ""
    body: str = ""
    allowedTools: list[str] = Field(default_factory=list)
    scope: Scope = "user"
    path: str = ""
    # Why this skill can't be used, if it can't. A skill that fails to load silently
    # is the worst outcome: the agent just doesn't know something, and nothing says why.
    error: str = ""
    # A project skill hidden by a user skill of the same name. `use_skill` resolves by
    # name, so the collision has to be visible or "I edited it and nothing changed"
    # has no explanation.
    shadowed: bool = False
    enabled: bool = True


class SkillListResponse(BaseModel):
    skills: list[SkillModel] = Field(default_factory=list)
    userDir: str = ""
    projectDir: str = ""


class SkillInput(BaseModel):
    name: str
    description: str = ""
    body: str = ""
    allowedTools: list[str] = Field(default_factory=list)


class EnableInput(BaseModel):
    enabled: bool = True


class SkillCostEntry(BaseModel):
    name: str
    # What this skill's catalog line costs on EVERY turn.
    tokens: int = 0
    # What its body costs, but only on a turn that calls `use_skill`.
    bodyTokens: int = 0


class SkillCostResponse(BaseModel):
    skills: list[SkillCostEntry] = Field(default_factory=list)
    catalogTokens: int = 0
    # False means chars/4 estimates. The pane must say so rather than imply precision.
    exact: bool = False
    tokenizer: str = ""


class PreviewResponse(BaseModel):
    """Exactly what the agent sees, in both tiers.

    The point of the pane: `catalog` is the text injected every turn, `instructions`
    is what arrives only when the model calls `use_skill`. Showing the assembled
    strings rather than describing them is what makes the cost concrete.
    """

    catalog: str = ""
    instructions: str = ""
    groups: list[str] = Field(default_factory=list)


class ExportResponse(BaseModel):
    path: str
