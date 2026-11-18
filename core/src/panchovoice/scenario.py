"""Scenario file models (contract section 2)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import FieldError, pydantic_errors

if TYPE_CHECKING:
    from .config import Config

KEBAB = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Turn(_Strict):
    say: str = Field(min_length=1)
    # barge-in: start this turn this many ms after the agent starts its reply
    interrupt_after_ms: int | None = Field(default=None, ge=0)
    # extension (decisions.md): extra silence before the caller speaks this turn
    pause_ms: int | None = Field(default=None, ge=0, le=120000)


class Traits(_Strict):
    interrupts: bool = False
    patience_s: float = Field(6.0, gt=0)


class Caller(_Strict):
    mode: Literal["scripted", "ai"] = "scripted"
    voice: str = "af_heart"
    speed: float = Field(1.0, gt=0.25, le=3.0)
    turns: list[Turn] | None = None
    persona: str | None = None
    goal: str | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    opening_line: str | None = None
    traits: Traits = Field(default_factory=Traits)

    @model_validator(mode="after")
    def _mode_fields(self) -> "Caller":
        if self.mode == "scripted" and not self.turns:
            raise ValueError("scripted mode needs a non empty `turns` list")
        if self.mode == "ai":
            missing = [k for k in ("persona", "goal") if not getattr(self, k)]
            if missing:
                raise ValueError(f"ai mode needs {' and '.join(missing)}")
        return self


class AudioOpts(_Strict):
    noise: Literal["none", "cafe", "street", "white"] = "none"
    snr_db: float = 20.0


class ExpectedToolCall(_Strict):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class LatencyExpect(_Strict):
    p50: int | None = Field(default=None, gt=0)
    p95: int | None = Field(default=None, gt=0)


class BargeInExpect(_Strict):
    max_stop_ms: int = Field(gt=0)


class Expect(_Strict):
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    no_extra_tools: bool = False
    order: list[str] = Field(default_factory=list)
    final_state: dict[str, list[dict[str, Any]]] | None = None
    latency_ms: LatencyExpect | None = None
    barge_in: BargeInExpect | None = None
    wer_max: float | None = Field(default=None, ge=0)
    must_say: list[str] = Field(default_factory=list)
    never_say: list[str] = Field(default_factory=list)
    judge: list[str] = Field(default_factory=list)


class Limits(_Strict):
    max_turns: int = Field(12, ge=1, le=200)
    max_seconds: int = Field(180, ge=5, le=3600)
    silence_timeout_s: float = Field(15, gt=0, le=300)


class Scenario(_Strict):
    id: str = Field(pattern=KEBAB)
    title: str = Field(min_length=1)
    agent: str
    language: Literal["en", "es"] = "en"
    risk: Literal["critical", "normal"] = "normal"
    tags: list[str] = Field(default_factory=list)
    fixture: str | None = None
    caller: Caller
    audio: AudioOpts = Field(default_factory=AudioOpts)
    expect: Expect = Field(default_factory=Expect)
    limits: Limits = Field(default_factory=Limits)
    runs: int = Field(1, ge=1, le=100)

    @property
    def has_interrupts(self) -> bool:
        return bool(self.caller.turns) and any(t.interrupt_after_ms is not None for t in self.caller.turns or [])


def validate_scenario_data(data: Any, filename: str | None = None) -> tuple[Scenario | None, list[FieldError]]:
    if not isinstance(data, dict):
        return None, [FieldError("", "the file must be a YAML mapping")]
    try:
        scn = Scenario.model_validate(data)
    except ValidationError as exc:
        return None, pydantic_errors(exc)
    if filename:
        stem = Path(filename).name
        for suf in (".yaml", ".yml"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
        if stem != scn.id:
            return scn, [FieldError("id", f"id '{scn.id}' must equal the file name '{stem}'")]
    return scn, []


def validate_scenario_text(text: str, filename: str | None = None) -> tuple[Scenario | None, list[FieldError]]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return None, [FieldError("", f"invalid YAML: {exc}")]
    return validate_scenario_data(data, filename)


def check_scenario_refs(scn: Scenario, config: "Config") -> list[FieldError]:
    """Cross file checks: the agent exists, the fixture exists, expected tools exist in the fixture."""
    from .fixture import load_fixture, resolve_fixture

    errors: list[FieldError] = []
    if config.agent(scn.agent) is None:
        known = ", ".join(a.id for a in config.agents) or "none"
        errors.append(FieldError("agent", f"agent '{scn.agent}' is not in panchovoice.yaml (known: {known})"))
    if scn.fixture:
        fpath = resolve_fixture(scn.fixture, config.fixtures_path)
        if not fpath.is_file():
            errors.append(FieldError("fixture", f"fixture file not found: {fpath}"))
        else:
            try:
                fx = load_fixture(fpath)
            except ValueError as exc:
                errors.append(FieldError("fixture", str(exc)))
            else:
                tool_names = {t.name for t in fx.tools}
                for i, tc in enumerate(scn.expect.tool_calls):
                    if tc.name not in tool_names:
                        errors.append(FieldError(f"expect.tool_calls.{i}.name", f"tool '{tc.name}' is not in fixture '{fx.id}'"))
                for i, name in enumerate(scn.expect.forbidden_tools):
                    if name not in tool_names:
                        errors.append(FieldError(f"expect.forbidden_tools.{i}", f"tool '{name}' is not in fixture '{fx.id}'"))
                for i, name in enumerate(scn.expect.order):
                    if name not in tool_names:
                        errors.append(FieldError(f"expect.order.{i}", f"tool '{name}' is not in fixture '{fx.id}'"))
    elif scn.expect.tool_calls or scn.expect.final_state:
        errors.append(FieldError("fixture", "expect.tool_calls or expect.final_state needs a fixture"))
    return errors


def load_scenario(path: str | Path, config: "Config | None" = None) -> Scenario:
    """Load and validate one scenario file. Raises ValueError with dotted paths on errors."""
    p = Path(path)
    scn, errors = validate_scenario_text(p.read_text(encoding="utf-8"), p.name)
    if scn is not None and not errors and config is not None:
        errors = check_scenario_refs(scn, config)
    if errors or scn is None:
        raise ValueError(f"{p}: " + "; ".join(str(e) for e in errors))
    return scn


def list_scenario_files(scenarios_dir: Path) -> list[Path]:
    if not scenarios_dir.is_dir():
        return []
    return sorted([*scenarios_dir.glob("*.yaml"), *scenarios_dir.glob("*.yml")])


def load_all_scenarios(config: "Config") -> tuple[list[Scenario], dict[str, list[FieldError]]]:
    """All scenarios in scenarios_dir; returns (valid scenarios, errors by file name)."""
    good: list[Scenario] = []
    bad: dict[str, list[FieldError]] = {}
    for f in list_scenario_files(config.scenarios_path):
        scn, errors = validate_scenario_text(f.read_text(encoding="utf-8"), f.name)
        if scn is not None and not errors:
            errors = check_scenario_refs(scn, config)
        if errors or scn is None:
            bad[f.name] = errors
        else:
            good.append(scn)
    return good, bad
