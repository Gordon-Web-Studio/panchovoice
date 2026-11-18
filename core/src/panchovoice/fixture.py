"""Fixture file models (contract section 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import FieldError, pydantic_errors


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requires(_Strict):
    collection: str
    match: list[str]


class ToolDef(_Strict):
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str = ""
    params: dict[str, Literal["string", "number", "integer", "boolean"]] = Field(default_factory=dict)
    action: Literal["find", "insert", "delete"]
    collection: str
    match: list[str] = Field(default_factory=list)
    returns: Literal["first_or_error", "all"] = "first_or_error"
    fields: list[str] | None = None
    requires: Requires | None = None

    @model_validator(mode="after")
    def _check(self) -> "ToolDef":
        for key in self.match:
            if key not in self.params:
                raise ValueError(f"match key '{key}' is not in params")
        if self.requires:
            for key in self.requires.match:
                if key not in self.params:
                    raise ValueError(f"requires.match key '{key}' is not in params")
        if self.action in ("find", "delete") and not self.match:
            raise ValueError(f"action '{self.action}' needs a non empty match list")
        return self

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "params": dict(self.params)}


class Fixture(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str = ""
    tools: list[ToolDef] = Field(default_factory=list)
    state: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Fixture":
        names = [t.name for t in self.tools]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate tool names: {sorted(dupes)}")
        for t in self.tools:
            if t.action in ("find", "delete") and t.collection not in self.state:
                raise ValueError(f"tool '{t.name}' uses collection '{t.collection}' that is not in state")
            if t.requires and t.requires.collection not in self.state:
                raise ValueError(f"tool '{t.name}' requires collection '{t.requires.collection}' that is not in state")
        return self

    def tool(self, name: str) -> ToolDef | None:
        for t in self.tools:
            if t.name == name:
                return t
        return None


def validate_fixture_text(text: str, filename: str | None = None) -> tuple[Fixture | None, list[FieldError]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [FieldError("", f"invalid JSON: {exc}")]
    if not isinstance(data, dict):
        return None, [FieldError("", "the file must be a JSON object")]
    try:
        fx = Fixture.model_validate(data)
    except ValidationError as exc:
        return None, pydantic_errors(exc)
    if filename:
        stem = Path(filename).stem
        if stem != fx.id:
            return fx, [FieldError("id", f"id '{fx.id}' must equal the file name '{stem}'")]
    return fx, []


def load_fixture(path: str | Path) -> Fixture:
    p = Path(path)
    fx, errors = validate_fixture_text(p.read_text(encoding="utf-8"), p.name)
    if errors or fx is None:
        raise ValueError(f"{p}: " + "; ".join(str(e) for e in errors))
    return fx


def resolve_fixture(name: str, fixtures_dir: Path) -> Path:
    """`acme-clinic.json` or `acme-clinic` -> path in fixtures_dir."""
    p = Path(name)
    if p.is_absolute():
        return p
    if p.suffix != ".json":
        p = p.with_suffix(".json")
    return fixtures_dir / p
