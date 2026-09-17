"""Text, JSON and SARIF reporters."""
from __future__ import annotations

import json
from dataclasses import asdict

from .. import __version__
from .graph import Graph
from .model import Violation


def text_report(
    violations: list[Violation],
    graphs: list[Graph],
    verbose: bool = False,
    baselined: list[Violation] | None = None,
    waived: list[Violation] | None = None,
    show_waived: bool = False,
) -> str:
    baselined = baselined or []
    waived = waived or []
    lines: list[str] = []
    by_project: dict[str, list[Violation]] = {}
    for v in violations:
        by_project.setdefault(v.project, []).append(v)
    for g in graphs:
        vs = by_project.get(g.project.name, [])
        errs = sum(1 for v in vs if v.severity == "error")
        infos = sum(1 for v in vs if v.severity == "info")
        warns = len(vs) - errs - infos
        layered = sum(1 for f in g.files.values() if f.layer)
        internal = sum(1 for e in g.edges if e.dst is not None)
        extra = []
        b = sum(1 for v in baselined if v.project == g.project.name)
        w = sum(1 for v in waived if v.project == g.project.name)
        if b:
            extra.append(f"{b} baselined")
        if w:
            extra.append(f"{w} waived")
        if infos:
            extra.append(f"{infos} info")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"[{g.project.name}] {len(g.files)} files ({layered} in layers), {internal} internal edges, {errs} error(s), {warns} warning(s){suffix}")
        if verbose:
            for (a, b_), edges in sorted(g.layer_edges().items()):
                if a != b_:
                    lines.append(f"    {a} -> {b_}: {len(edges)}")
        for v in sorted(vs, key=lambda v: ({"error": 0, "warning": 1}.get(v.severity, 2), v.path, v.line)):
            lines.append(f"  {v.path}:{v.line}: {v.severity}[{v.rule}] {v.message}")
        if show_waived:
            for v in sorted((v for v in waived if v.project == g.project.name), key=lambda v: (v.path, v.line)):
                lines.append(f"  {v.path}:{v.line}: waived[{v.rule}] {v.message}")
    errors = sum(1 for v in violations if v.severity == "error")
    infos = sum(1 for v in violations if v.severity == "info")
    warnings = len(violations) - errors - infos
    lines.append("")
    tail = []
    if warnings:
        tail.append(f"{warnings} warning(s)")
    if infos:
        tail.append(f"{infos} info")
    if baselined:
        tail.append(f"{len(baselined)} baselined")
    if waived:
        tail.append(f"{len(waived)} waived")
    note = f" ({', '.join(tail)})" if tail else ""
    lines.append(f"OK: no architecture errors{note}" if errors == 0 else f"FAIL: {errors} error(s){note}")
    return "\n".join(lines)


def json_report(violations: list[Violation], graphs: list[Graph], baselined: list[Violation] | None = None, waived: list[Violation] | None = None) -> str:
    return json.dumps(
        {
            "version": __version__,
            "summary": {
                "errors": sum(1 for v in violations if v.severity == "error"),
                "warnings": sum(1 for v in violations if v.severity == "warning"),
                "info": sum(1 for v in violations if v.severity == "info"),
                "baselined": len(baselined or []),
                "waived": len(waived or []),
            },
            "projects": [
                {
                    "name": g.project.name,
                    "files": len(g.files),
                    "layers": {l.name: sum(1 for f in g.files.values() if f.layer == l.name) for l in g.project.layers},
                    "layer_edges": [{"from": a, "to": b, "count": len(e)} for (a, b), e in sorted(g.layer_edges().items())],
                }
                for g in graphs
            ],
            "violations": [asdict(v) for v in violations],
            "baselined": [asdict(v) for v in (baselined or [])],
            "waived": [asdict(v) for v in (waived or [])],
        },
        indent=2,
    )


def sarif_report(violations: list[Violation]) -> str:
    rule_ids = sorted({v.rule for v in violations} | {"forbidden-import", "forbidden-external", "layer-cycle", "unlayered"})
    rules = [
        {"id": rid, "shortDescription": {"text": rid.replace("-", " ")}, "defaultConfiguration": {"level": "error"}}
        for rid in rule_ids
    ]
    idx = {rid: i for i, rid in enumerate(rule_ids)}
    results = [
        {
            "ruleId": v.rule,
            "ruleIndex": idx[v.rule],
            "level": {"warning": "warning", "info": "note"}.get(v.severity, "error"),
            "message": {"text": v.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": v.path, "uriBaseId": "%SRCROOT%"},
                        "region": {"startLine": max(v.line, 1)},
                    }
                }
            ],
            "properties": {"project": v.project, "sourceLayer": v.src_layer, "targetLayer": v.dst_layer},
        }
        for v in violations
    ]
    return json.dumps(
        {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [{"tool": {"driver": {"name": "archfence", "version": __version__, "informationUri": "https://github.com/", "rules": rules}}, "results": results}],
        },
        indent=2,
    )
