"""Shared error helpers: validation errors with a dotted path."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from pydantic import ValidationError


@dataclass
class FieldError:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


def loc_to_path(loc: tuple[Any, ...]) -> str:
    """Turn a pydantic error location into a dotted path like `caller.turns.0.say`."""
    parts: list[str] = []
    for item in loc:
        # pydantic adds model or union tags (for example "function-after[...]"); skip them
        if isinstance(item, str) and ("[" in item and item.endswith("]")):
            continue
        parts.append(str(item))
    return ".".join(parts)


def pydantic_errors(exc: ValidationError) -> list[FieldError]:
    out: list[FieldError] = []
    for err in exc.errors(include_url=False):
        msg = err.get("msg", "invalid")
        if err.get("type") == "extra_forbidden":
            msg = "unknown key"
        out.append(FieldError(loc_to_path(tuple(err.get("loc", ()))), msg))
    return out


class PanchoError(Exception):
    """Base error of the engine."""


class ConfigError(PanchoError):
    def __init__(self, errors: list[FieldError], source: str = "panchovoice.yaml"):
        self.errors = errors
        self.source = source
        super().__init__(f"{source}: " + "; ".join(str(e) for e in errors))
