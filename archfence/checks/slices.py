"""Vertical slices: every directory under a slice root is a feature that must not import its siblings."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Iterable

from ..core.config import ConfigError, reject_unknown, severity, string_list
from ..core.globs import glob_match
from ..core.graph import Graph
from ..core.model import Violation
from .base import Check, Proposal, ProposeContext

SLICE_DIR_NAMES = {"features", "feature", "slices", "modules", "verticals"}
SHARED_LAYER_NAMES = ("core", "shared", "common", "kernel", "lib", "utils", "infrastructure")


@dataclass
class SlicesConfig:
    roots: list[str]  # globs whose direct children are slices, e.g. "app/features/*"
    shared: list[str] = field(default_factory=list)  # code any slice may use
    shared_only: bool = False  # a slice may import nothing outside itself and `shared`
    shared_imports_slices: str = "error"  # severity when shared code depends on a slice
    severity: str = "error"
    allow: dict[str, list[str]] = field(default_factory=dict)  # slice -> slices it may import (explicit exceptions)

    def is_shared(self, rel_path: str) -> bool:
        return any(glob_match(rel_path, g) for g in self.shared)

    def slice_for(self, rel_path: str) -> str | None:
        """Slice name for a path, or None. The slice is the path segment right after the root glob.
        Anything matching `shared` is shared, never a slice, even when it sits under a slice root."""
        if self.is_shared(rel_path):
            return None
        parts = rel_path.split("/")
        for root in self.roots:
            rparts = [x for x in root.rstrip("/").split("/") if x]
            if rparts and rparts[-1] == "*":
                rparts = rparts[:-1]
            n = len(rparts)
            if len(parts) > n + 1 and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts[:n], rparts)):
                return parts[n]
        return None


class SlicesCheck(Check):
    key = "slices"

    def parse(self, raw_project: dict, ctx: str, layer_names: set[str]) -> SlicesConfig | None:
        raw = raw_project.get("slices")
        if not raw:
            return None
        if isinstance(raw, str):
            raw = {"roots": [raw]}
        if isinstance(raw, list):
            raw = {"roots": raw}
        reject_unknown(raw, {"roots", "root", "shared", "shared_only", "shared_imports_slices", "severity", "allow"}, f"{ctx}: slices")
        roots = string_list(raw.get("roots") or raw.get("root"))
        if not roots:
            raise ConfigError(f"{ctx}: slices needs 'roots' (globs whose children are the slices, e.g. app/features/*)")
        allow = raw.get("allow") or {}
        if not isinstance(allow, dict):
            raise ConfigError(f"{ctx}: slices.allow must map a slice to the slices it may import")
        return SlicesConfig(
            roots=roots,
            shared=string_list(raw.get("shared")),
            shared_only=bool(raw.get("shared_only", False)),
            shared_imports_slices=severity(raw.get("shared_imports_slices"), f"{ctx} slices.shared_imports_slices", "error"),
            severity=severity(raw.get("severity"), f"{ctx} slices.severity", "error"),
            allow={str(k): string_list(v) for k, v in allow.items()},
        )

    def run(self, graph: Graph, cfg: SlicesConfig) -> Iterable[Violation]:
        """A slice may import itself and shared code. Shared code never imports a slice."""
        name = graph.project.name
        for edge in graph.edges:
            if edge.dst is None:
                continue
            src_slice, dst_slice = cfg.slice_for(edge.src), cfg.slice_for(edge.dst)
            via = edge.dst if edge.target == edge.dst else f"{edge.target} -> {edge.dst}"
            if src_slice is not None:
                if dst_slice is not None and dst_slice != src_slice:
                    if dst_slice in cfg.allow.get(src_slice, []):
                        continue
                    yield Violation("slice-coupling", f"slice '{src_slice}' must not import slice '{dst_slice}' ({via})", edge.src, edge.line, f"slice:{src_slice}", f"slice:{dst_slice}", edge.target, name, cfg.severity)
                elif dst_slice is None and cfg.shared_only and not cfg.is_shared(edge.dst):
                    yield Violation("slice-escape", f"slice '{src_slice}' may only import shared code, not {via}", edge.src, edge.line, f"slice:{src_slice}", graph.files[edge.dst].layer, edge.target, name, cfg.severity)
            elif dst_slice is not None and cfg.is_shared(edge.src):
                yield Violation("shared-imports-slice", f"shared code must not depend on slice '{dst_slice}' ({via})", edge.src, edge.line, graph.files[edge.src].layer, f"slice:{dst_slice}", edge.target, name, cfg.shared_imports_slices)

    def propose(self, ctx: ProposeContext) -> Proposal | None:
        """A directory named features/slices/modules with three or more child packages is a slice root."""
        for d in sorted(ctx.project_root.rglob("*")):
            if not d.is_dir() or d.name not in SLICE_DIR_NAMES or any(x.startswith(".") or x == "node_modules" for x in d.relative_to(ctx.project_root).parts):
                continue
            children = [c for c in d.iterdir() if c.is_dir() and not c.name.startswith(("_", ".")) and any(c.rglob("*.*"))]
            if len(children) >= 3:
                rel = d.relative_to(ctx.project_root).as_posix()
                spec: dict = {"roots": [f"{rel}/*"], "severity": "warning"}
                shared = [g for n, g in ctx.layer_globs.items() if n in SHARED_LAYER_NAMES]
                if shared:
                    spec["shared"] = shared
                return Proposal({"slices": spec}, [f"vertical slices under {rel}/: slices must not import each other"])
        return None
