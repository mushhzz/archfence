"""Configuration: the project shell every check plugs into.

Core owns what all checks share: projects, layers, waivers, presets and severities.
Each check parses its own block (``slices:``, ``contracts:`` ...) by registering a parser through
``register_check_parser``; core never imports a check. The package root wires them up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from .globs import glob_match, specificity

PRESET_DIR = Path(__file__).parent.parent / "presets"
SEVERITIES = ("error", "warning", "info", "off")


class ConfigError(Exception):
    pass


# ---------------------------------------------------------------- shared parsing helpers


def severity(value, ctx: str, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, bool):  # YAML reads a bare `off` as False and `on` as True
        return "off" if not value else "error"
    v = str(value).lower()
    if v == "warn":
        v = "warning"
    if v not in SEVERITIES:
        raise ConfigError(f"{ctx}: severity must be one of {SEVERITIES}, got '{value}'")
    return v


def subjects(raw, ctx: str, default: str) -> dict[str, str]:
    """Accept ``[a, b]`` (all at the default severity) or ``{a: warning, b: error}``."""
    if raw is None:
        return {}
    if isinstance(raw, list):
        return {str(x): default for x in raw}
    if isinstance(raw, dict):
        return {str(k): severity(v, f"{ctx} '{k}'", default) for k, v in raw.items()}
    if isinstance(raw, str):
        return {raw: default}
    raise ConfigError(f"{ctx}: expected a list or a mapping of name -> severity")


def optional_list(spec: dict, key: str) -> list[str] | None:
    if key not in spec or spec[key] is None:
        return None
    value = spec[key]
    if isinstance(value, str):
        value = [value]
    return [str(x) for x in value]


def string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(x) for x in value]


# ---------------------------------------------------------------- the port checks plug into


class CheckParser(Protocol):
    key: str

    def parse(self, raw_project: dict, ctx: str, layer_names: set[str]) -> Any | None: ...


CHECK_PARSERS: list[CheckParser] = []


def register_check_parser(check: CheckParser) -> None:
    """Called by the checks package at import time; core stays ignorant of which checks exist."""
    if all(c.key != check.key for c in CHECK_PARSERS):
        CHECK_PARSERS.append(check)


# ---------------------------------------------------------------- core model


@dataclass
class Waiver:
    path: str
    reason: str
    layers: list[str] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def covers(self, rel_path: str, rule: str, dst_layer: str | None, target: str) -> bool:
        if not glob_match(rel_path, self.path):
            return False
        if self.rules and rule not in self.rules:
            return False
        if self.layers and dst_layer not in self.layers:
            return False
        if self.external and not any(target == p or target.startswith(p) for p in self.external):
            return False
        return True


@dataclass
class LayerConfig:
    name: str
    paths: list[str]
    severity: str = "error"
    # Each rule maps its subject (a layer name or an external prefix) to a severity.
    cannot_import: dict[str, str] = field(default_factory=dict)
    can_only_import: list[str] | None = None
    cannot_import_external: dict[str, str] = field(default_factory=dict)
    can_only_import_external: list[str] | None = None

    def matches(self, rel_path: str) -> bool:
        return any(glob_match(rel_path, p) for p in self.paths)

    def specificity(self, rel_path: str) -> tuple[int, int] | None:
        return specificity(rel_path, self.paths)


@dataclass
class ProjectConfig:
    name: str
    root: str
    languages: list[str]
    layers: list[LayerConfig]
    ignore: list[str] = field(default_factory=list)
    source_roots: list[str] = field(default_factory=list)
    strict: bool = False
    waivers: list[Waiver] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)  # check key -> that check's parsed config

    def layer_for(self, rel_path: str) -> str | None:
        """Most specific matching layer wins; config order breaks ties."""
        best_name, best_score = None, None
        for layer in self.layers:
            score = layer.specificity(rel_path)
            if score is not None and (best_score is None or score > best_score):
                best_name, best_score = layer.name, score
        return best_name

    def layer(self, name: str) -> LayerConfig:
        for layer in self.layers:
            if layer.name == name:
                return layer
        raise KeyError(name)

    def layer_names(self) -> list[str]:
        return [l.name for l in self.layers]

    def is_ignored(self, rel_path: str) -> bool:
        return any(glob_match(rel_path, p) for p in self.ignore)

    def check(self, key: str) -> Any | None:
        return self.checks.get(key)

    def effective(self, sev: str) -> str:
        """Apply `strict`: warnings become errors. Info is advisory by definition and never promoted."""
        if self.strict and sev == "warning":
            return "error"
        return sev


@dataclass
class Config:
    projects: list[ProjectConfig]
    path: Path


# ---------------------------------------------------------------- loading


def _load_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def preset_names() -> list[str]:
    return sorted(p.stem for p in PRESET_DIR.glob("*.yml"))


def _resolve_preset(name: str) -> dict:
    candidate = PRESET_DIR / f"{name}.yml"
    if not candidate.exists():
        raise ConfigError(f"unknown preset '{name}'. Available: {', '.join(preset_names())}")
    return _load_yaml(candidate)


def _merge(base: dict, override: dict) -> dict:
    """Shallow merge for top level, per-layer merge for ``layers``."""
    out = dict(base)
    for key, value in override.items():
        if key == "layers" and isinstance(value, dict) and isinstance(base.get("layers"), dict):
            merged = {k: dict(v or {}) for k, v in base["layers"].items()}
            for lname, lval in value.items():
                merged.setdefault(lname, {}).update(lval or {})
            out["layers"] = merged
        else:
            out[key] = value
    return out


def _parse_layers(raw: dict, ctx: str) -> list[LayerConfig]:
    layers: list[LayerConfig] = []
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{ctx}: 'layers' must be a non-empty mapping of layer name -> settings")
    for name, spec in raw.items():
        spec = spec or {}
        paths = string_list(spec.get("paths") or spec.get("path"))
        if not paths:
            raise ConfigError(f"{ctx}: layer '{name}' needs 'paths'")
        lctx = f"{ctx}, layer '{name}'"
        default = severity(spec.get("severity"), lctx, "error")
        layers.append(
            LayerConfig(
                name=str(name),
                paths=paths,
                severity=default,
                cannot_import=subjects(spec.get("cannot_import"), f"{lctx} cannot_import", default),
                can_only_import=optional_list(spec, "can_only_import"),
                cannot_import_external=subjects(spec.get("cannot_import_external"), f"{lctx} cannot_import_external", default),
                can_only_import_external=optional_list(spec, "can_only_import_external"),
            )
        )
    names = {l.name for l in layers}
    for layer in layers:
        for ref in list(layer.cannot_import) + (layer.can_only_import or []):
            if ref not in names:
                raise ConfigError(f"{ctx}: layer '{layer.name}' references unknown layer '{ref}'")
    return layers


def _parse_waivers(raw, ctx: str, layer_names: set[str]) -> list[Waiver]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ConfigError(f"{ctx}: 'allow' must be a list")
    out: list[Waiver] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict) or not item.get("path"):
            raise ConfigError(f"{ctx}: allow[{i}] needs a 'path' glob")
        reason = str(item.get("reason") or "").strip()
        if not reason:
            raise ConfigError(f"{ctx}: allow[{i}] ({item['path']}) needs a 'reason' - waivers must document themselves")
        layers = optional_list(item, "layers") or ([] if "layer" not in item else [str(item["layer"])])
        for l in layers:
            if l not in layer_names:
                raise ConfigError(f"{ctx}: allow[{i}] references unknown layer '{l}'")
        out.append(Waiver(path=str(item["path"]), reason=reason, layers=layers, external=optional_list(item, "external") or [], rules=optional_list(item, "rules") or []))
    return out


def parse_project(raw: dict, default_name: str) -> ProjectConfig:
    """Build a ProjectConfig from one project's mapping. Each registered check parses its own block."""
    if "extends" in raw:
        raw = _merge(_resolve_preset(str(raw["extends"])), {k: v for k, v in raw.items() if k != "extends"})
    name = str(raw.get("name") or default_name)
    ctx = f"project '{name}'"
    languages = string_list(raw.get("languages") or raw.get("language"))
    if not languages:
        raise ConfigError(f"{ctx}: 'languages' is required (csharp, python, typescript, dart, rust)")
    layers = _parse_layers(raw.get("layers"), ctx)
    layer_names = {l.name for l in layers}
    project = ProjectConfig(
        name=name,
        root=str(raw.get("root") or "."),
        languages=languages,
        layers=layers,
        ignore=string_list(raw.get("ignore")),
        source_roots=string_list(raw.get("source_roots")),
        strict=bool(raw.get("strict", False)),
        waivers=_parse_waivers(raw.get("allow"), ctx, layer_names),
    )
    for check in CHECK_PARSERS:
        cfg = check.parse(raw, ctx, layer_names)
        if cfg is not None:
            project.checks[check.key] = cfg
    return project


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = _load_yaml(path)
    projects: list[ProjectConfig] = []
    if "projects" in data:
        raw_projects = data["projects"]
        if isinstance(raw_projects, dict):
            items = [(k, dict(v or {}, name=k)) for k, v in raw_projects.items()]
        elif isinstance(raw_projects, list):
            items = [(str(p.get("name", f"project{i}")), p) for i, p in enumerate(raw_projects)]
        else:
            raise ConfigError(f"{path}: 'projects' must be a list or mapping")
        for default_name, raw in items:
            projects.append(parse_project(raw, default_name))
    else:
        projects.append(parse_project(data, path.parent.name or "default"))
    return Config(projects=projects, path=path)
