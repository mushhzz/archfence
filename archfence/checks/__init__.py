"""Check registry and the engine that runs every enabled check over a graph."""
from __future__ import annotations

from ..core.config import register_check_parser
from ..core.graph import Graph
from ..core.model import Violation
from .base import Check, Proposal, ProposeContext
from .contracts import ContractsCheck
from .layers import LayersCheck
from .slices import SlicesCheck

CHECKS: list[Check] = [LayersCheck(), SlicesCheck(), ContractsCheck()]
for _check in CHECKS:
    register_check_parser(_check)


def run_checks(graph: Graph) -> tuple[list[Violation], list[Violation]]:
    """Run every check the project enables. Returns (violations, waived); severity 'off' is dropped."""
    project = graph.project
    seen: set[tuple] = set()
    violations: list[Violation] = []
    for check in CHECKS:
        cfg = project.check(check.key)
        if cfg is None:
            continue
        for v in check.run(graph, cfg):
            sev = project.effective(v.severity)
            if sev == "off":
                continue
            key = (v.rule, v.path, v.line, v.target, v.dst_layer)
            if key in seen:
                continue
            seen.add(key)
            violations.append(v if sev == v.severity else Violation(v.rule, v.message, v.path, v.line, v.src_layer, v.dst_layer, v.target, v.project, sev))
    kept: list[Violation] = []
    waived: list[Violation] = []
    for v in violations:
        (waived if any(w.covers(v.path, v.rule, v.dst_layer, v.target) for w in project.waivers) else kept).append(v)
    return kept, waived


__all__ = ["CHECKS", "Check", "Proposal", "ProposeContext", "run_checks"]
