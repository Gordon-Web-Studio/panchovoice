"""Validate scenario and fixture files (used by `pv validate` and the API server)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .errors import FieldError
from .fixture import validate_fixture_text
from .scenario import check_scenario_refs, list_scenario_files, validate_scenario_text


@dataclass
class FileReport:
    path: Path
    kind: str  # scenario | fixture | unknown
    errors: list[FieldError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_file(path: Path, config: Config) -> FileReport:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        _fx, errors = validate_fixture_text(text, path.name)
        return FileReport(path, "fixture", errors)
    if path.suffix in (".yaml", ".yml"):
        scn, errors = validate_scenario_text(text, path.name)
        if scn is not None and not errors:
            errors = check_scenario_refs(scn, config)
        return FileReport(path, "scenario", errors)
    return FileReport(path, "unknown", [FieldError("", "not a .yaml, .yml or .json file")])


def collect_paths(paths: list[Path], config: Config) -> list[Path]:
    if not paths:
        files = list_scenario_files(config.scenarios_path)
        if config.fixtures_path.is_dir():
            files += sorted(config.fixtures_path.glob("*.json"))
        return files
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out += sorted([*p.glob("*.yaml"), *p.glob("*.yml"), *p.glob("*.json")])
        else:
            out.append(p)
    return out


def validate_paths(paths: list[Path], config: Config) -> list[FileReport]:
    reports = []
    for p in collect_paths(paths, config):
        if not p.exists():
            reports.append(FileReport(p, "unknown", [FieldError("", "file not found")]))
        else:
            reports.append(validate_file(p, config))
    return reports
