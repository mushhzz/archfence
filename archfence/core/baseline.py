"""Baseline: record today's violations so only new ones fail the build."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import __version__
from .model import Violation

DEFAULT_NAME = "archfence-baseline.json"


def _cycle_layers(message: str) -> str:
    """The set of layers named in a layer-cycle message, sorted and joined: a fingerprint that does not
    change when the cycle is re-detected through a different representative edge or file."""
    tail = message.split(":", 1)[-1]
    return ",".join(sorted({s.strip() for s in tail.split("->") if s.strip()}))


def _key(project: str, rule: str, path: str, target: str, layer: str | None, message: str) -> str:
    # A layer cycle is identified by the layers it spans, not by which edge happened to represent it,
    # so moving the offending import between files does not resurrect a baselined cycle.
    if rule == "layer-cycle":
        return "|".join([project, rule, _cycle_layers(message)])
    # Line numbers are deliberately left out so unrelated edits above a violation do not churn it.
    return "|".join([project, rule, path, target, layer or ""])


def key(v: Violation) -> str:
    return _key(v.project, v.rule, v.path, v.target, v.dst_layer, v.message)


def write(path: Path, violations: list[Violation]) -> int:
    entries = sorted({key(v): {"project": v.project, "rule": v.rule, "path": v.path, "target": v.target, "layer": v.dst_layer, "message": v.message} for v in violations}.items())
    path.write_text(
        json.dumps({"archfence": __version__, "created": date.today().isoformat(), "violations": [e for _, e in entries]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries)


def load(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {_key(e["project"], e["rule"], e["path"], e["target"], e.get("layer"), e.get("message", "")) for e in data.get("violations", [])}


def split(violations: list[Violation], known: set[str]) -> tuple[list[Violation], list[Violation]]:
    """Return (new, baselined)."""
    new, old = [], []
    for v in violations:
        (old if key(v) in known else new).append(v)
    return new, old
