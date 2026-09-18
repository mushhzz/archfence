"""Coupling metrics as gate-able rules: fan-in, fan-out, instability and dead code.

archfence already resolves every import to a project file, so afferent/efferent coupling is a count over
that edge set -- and it works the same for all five languages. Unlike a metrics-only tool, a threshold here
is an ordinary rule: it carries a severity and is subject to `strict`, waivers and the baseline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..core.config import ConfigError, reject_unknown, severity, string_list
from ..core.globs import glob_match
from ..core.graph import Graph
from ..core.model import Violation
from .base import Check, Proposal, ProposeContext


@dataclass
class Threshold:
    limit: int
    severity: str = "warning"


@dataclass
class DeadCodeConfig:
    severity: str = "warning"
    entrypoints: list[str] = field(default_factory=list)  # files that are meant to have no importers
    exclude: list[str] = field(default_factory=list)


@dataclass
class GodClassConfig:
    max_methods: int
    severity: str = "warning"


@dataclass
class MetricsConfig:
    fan_out: Threshold | None = None
    fan_in: Threshold | None = None
    dead_code: DeadCodeConfig | None = None
    god_class: GodClassConfig | None = None


def couplings(graph: Graph) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(efferent, afferent): file -> the distinct project files it depends on / that depend on it."""
    efferent: dict[str, set[str]] = {p: set() for p in graph.files}
    afferent: dict[str, set[str]] = {p: set() for p in graph.files}
    for e in graph.edges:
        if e.dst is None or e.src == e.dst:
            continue
        efferent[e.src].add(e.dst)
        afferent[e.dst].add(e.src)
    return efferent, afferent


def instability(ce: int, ca: int) -> float:
    """Martin's instability I = Ce / (Ca + Ce); 0 is maximally stable, 1 maximally unstable."""
    total = ce + ca
    return ce / total if total else 0.0


def _threshold(raw, ctx: str) -> Threshold | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return Threshold(int(raw))
    if isinstance(raw, dict):
        reject_unknown(raw, {"max", "severity"}, ctx)
        if "max" not in raw:
            raise ConfigError(f"{ctx}: needs 'max' (the highest allowed count)")
        return Threshold(int(raw["max"]), severity(raw.get("severity"), ctx, "warning"))
    raise ConfigError(f"{ctx}: expected a number or a mapping with 'max' and optional 'severity'")


class MetricsCheck(Check):
    key = "metrics"

    def parse(self, raw_project: dict, ctx: str, layer_names: set[str]) -> MetricsConfig | None:
        raw = raw_project.get("metrics")
        if not raw:
            return None
        if not isinstance(raw, dict):
            raise ConfigError(f"{ctx}: metrics must be a mapping (fan_out, fan_in, dead_code)")
        reject_unknown(raw, {"fan_out", "fan_in", "dead_code", "god_class"}, f"{ctx}: metrics")
        god = None
        gc = raw.get("god_class")
        if gc is not None:
            gctx = f"{ctx}: metrics.god_class"
            if isinstance(gc, (int, float)) and not isinstance(gc, bool):
                god = GodClassConfig(int(gc))
            elif isinstance(gc, dict):
                reject_unknown(gc, {"max_methods", "severity"}, gctx)
                if "max_methods" not in gc:
                    raise ConfigError(f"{gctx}: needs 'max_methods'")
                god = GodClassConfig(int(gc["max_methods"]), severity(gc.get("severity"), gctx, "warning"))
            else:
                raise ConfigError(f"{gctx}: expected a number or a mapping with 'max_methods'")
        dead = None
        dc = raw.get("dead_code")
        if dc:
            if not isinstance(dc, dict):
                dc = {}
            reject_unknown(dc, {"severity", "entrypoints", "exclude"}, f"{ctx}: metrics.dead_code")
            dead = DeadCodeConfig(
                severity=severity(dc.get("severity"), f"{ctx}: metrics.dead_code.severity", "warning"),
                entrypoints=string_list(dc.get("entrypoints")),
                exclude=string_list(dc.get("exclude")),
            )
        return MetricsConfig(
            fan_out=_threshold(raw.get("fan_out"), f"{ctx}: metrics.fan_out"),
            fan_in=_threshold(raw.get("fan_in"), f"{ctx}: metrics.fan_in"),
            dead_code=dead,
            god_class=god,
        )

    def run(self, graph: Graph, cfg: MetricsConfig) -> Iterable[Violation]:
        name = graph.project.name
        efferent, afferent = couplings(graph)
        if cfg.fan_out is not None:
            for path, deps in efferent.items():
                if len(deps) > cfg.fan_out.limit:
                    yield Violation("high-fan-out", f"{path} depends on {len(deps)} project files (fan-out limit {cfg.fan_out.limit}): a hub that is hard to change in isolation", path, 1, graph.files[path].layer, None, "", name, cfg.fan_out.severity)
        if cfg.fan_in is not None:
            for path, deps in afferent.items():
                if len(deps) > cfg.fan_in.limit:
                    yield Violation("high-fan-in", f"{path} is depended on by {len(deps)} project files (fan-in limit {cfg.fan_in.limit}): a change here ripples widely", path, 1, graph.files[path].layer, None, "", name, cfg.fan_in.severity)
        if cfg.dead_code is not None:
            for path in dead_code(graph, afferent, cfg.dead_code):
                yield Violation("dead-code", f"{path} is never imported by any project file and is not listed as an entrypoint", path, 1, graph.files[path].layer, None, "", name, cfg.dead_code.severity)
        if cfg.god_class is not None:
            for path, sf in graph.files.items():
                for t in sf.types:
                    if t.methods > cfg.god_class.max_methods:
                        yield Violation("god-class", f"{t.kind} '{t.name}' has {t.methods} methods (limit {cfg.god_class.max_methods}): likely more than one responsibility", path, t.line, sf.layer, None, "", name, cfg.god_class.severity)

    def propose(self, ctx: ProposeContext) -> Proposal | None:
        return None  # opt-in: thresholds are project judgement, not inferred from the tree


def dead_code(graph: Graph, afferent: dict[str, set[str]], cfg: DeadCodeConfig) -> list[str]:
    """Files that provide an importable name yet nothing in the project imports, minus declared entrypoints.

    A file with routes is treated as an entrypoint: the framework calls it, so having no importer is expected.
    """
    out: list[str] = []
    for path, sf in graph.files.items():
        if not sf.provides or afferent.get(path):
            continue
        if sf.routes:
            continue
        if any(glob_match(path, g) for g in cfg.entrypoints) or any(glob_match(path, g) for g in cfg.exclude):
            continue
        out.append(path)
    return sorted(out)
