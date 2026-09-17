"""Baseline: record today's violations so only new ones fail the build."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import __version__
from .model import Violation

DEFAULT_NAME = "archfence-baseline.json"


def key(v: Violation) -> str:
    # Line numbers are deliberately left out so unrelated edits above a violation do not churn it.
    return "|".join([v.project, v.rule, v.path, v.target, v.dst_layer or ""])


def write(path: Path, violations: list[Violation]) -> int:
    entries = sorted({key(v): {"project": v.project, "rule": v.rule, "path": v.path, "target": v.target, "layer": v.dst_layer, "message": v.message} for v in violations}.items())
    path.write_text(
        json.dumps({"archfence": __version__, "created": date.today().isoformat(), "violations": [e for _, e in entries]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries)


def load(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"|".join([e["project"], e["rule"], e["path"], e["target"], e.get("layer") or ""]) for e in data.get("violations", [])}


def split(violations: list[Violation], known: set[str]) -> tuple[list[Violation], list[Violation]]:
    """Return (new, baselined)."""
    new, old = [], []
    for v in violations:
        (old if key(v) in known else new).append(v)
    return new, old
