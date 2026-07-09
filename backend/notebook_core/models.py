"""Pydantic models for the notebook wire/UI boundary (nbformat-shaped)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CellModel(BaseModel):
    """One notebook cell, mirroring nbformat (outputs stay raw nbformat dicts)."""

    id: str
    cell_type: Literal["code", "markdown"]
    source: str
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    execution_count: int | None = None


class NotebookModel(BaseModel):
    path: str  # relative to the notebook's root (project root, notebook root, …)
    cells: list[CellModel]
    metadata: dict[str, Any] = Field(default_factory=dict)
