"""Shared schema primitives."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
