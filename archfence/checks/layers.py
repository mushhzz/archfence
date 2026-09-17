"""Layer dependency rules: cannot_import, can_only_import, external bans, unlayered files and layer cycles.

Also owns the vocabulary that `init` uses to draft these rules from layer names."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..core.config import severity
from ..core.graph import Graph
from ..core.model import Violation
from .base import Check, Proposal, ProposeContext


@dataclass
class LayersConfig:
    no_cycles: str = "error"  # a severity, or "off"
    allow_unlayered: bool = True


def match_prefix(target: str, prefixes) -> str | None:
    """Return the prefix that matches ``target`` (longest first), or None."""
    for p in sorted(prefixes, key=len, reverse=True):
        if p.endswith(("/", ".", ":")) and target.startswith(p):
            return p
        if target == p or target.startswith((p + ".", p + "::", p + "/", p + ":")):
            return p
        if p.endswith("*") and target.startswith(p[:-1]):
            return p
    return None


class LayersCheck(Check):
    key = "layers"

    def parse(self, raw: dict, ctx: str, layer_names: set[str]) -> LayersConfig:
        no_cycles = raw.get("no_cycles", True)
        if isinstance(no_cycles, bool):
            no_cycles = "error" if no_cycles else "off"
        return LayersConfig(no_cycles=severity(no_cycles, f"{ctx} no_cycles", "error"), allow_unlayered=bool(raw.get("allow_unlayered", True)))

    def run(self, graph: Graph, cfg: LayersConfig) -> Iterable[Violation]:
        project = graph.project
        name = project.name
        for edge in graph.edges:
            src = graph.files[edge.src]
            if src.layer is None:
                if not cfg.allow_unlayered:
                    yield Violation("unlayered", f"{edge.src} is not assigned to any layer", edge.src, 1, None, None, "", name, "warning")
                continue
            layer = project.layer(src.layer)

            if edge.dst is None:
                hit = match_prefix(edge.target, layer.cannot_import_external)
                if hit is not None:
                    yield Violation("forbidden-external", f"layer '{layer.name}' must not depend on '{edge.target}'", edge.src, edge.line, layer.name, None, edge.target, name, layer.cannot_import_external[hit])
                elif layer.can_only_import_external is not None and match_prefix(edge.target, layer.can_only_import_external) is None:
                    yield Violation("forbidden-external", f"layer '{layer.name}' may only depend on {layer.can_only_import_external}, not '{edge.target}'", edge.src, edge.line, layer.name, None, edge.target, name, layer.severity)
                continue

            dst_layer = graph.files[edge.dst].layer
            if dst_layer is None or dst_layer == layer.name:
                continue
            via = edge.dst if edge.target == edge.dst else f"{edge.target} -> {edge.dst}"
            if dst_layer in layer.cannot_import:
                yield Violation("forbidden-import", f"layer '{layer.name}' must not import from layer '{dst_layer}' ({via})", edge.src, edge.line, layer.name, dst_layer, edge.target, name, layer.cannot_import[dst_layer])
            elif layer.can_only_import is not None and dst_layer not in layer.can_only_import:
                yield Violation("forbidden-import", f"layer '{layer.name}' may only import from {layer.can_only_import}; found '{dst_layer}' ({via})", edge.src, edge.line, layer.name, dst_layer, edge.target, name, layer.severity)

        if cfg.no_cycles != "off":
            for cycle, sample in find_layer_cycles(graph):
                path = " -> ".join(cycle + [cycle[0]])
                yield Violation("layer-cycle", f"dependency cycle between layers: {path}", sample.src, sample.line, graph.files[sample.src].layer, graph.files[sample.dst].layer if sample.dst else None, sample.target, name, cfg.no_cycles)

    def propose(self, ctx: ProposeContext) -> Proposal:
        rules, notes = infer_rules(ctx.layer_names, ctx.matrix)
        acyclic = is_acyclic(ctx.layer_names, ctx.matrix)
        fragment: dict = {"layers": {n: r for n, r in rules.items() if r}, "no_cycles": "warning" if acyclic else False}
        if not acyclic:
            notes.append("no_cycles left off: the layer graph has cycles today (see the matrix)")
        return Proposal(fragment, notes)


# ---------------------------------------------------------------- cycles


def find_layer_cycles(graph: Graph):
    """Tarjan SCC over the layer graph; yields (cycle_layers, one representative edge)."""
    le = graph.layer_edges()
    adj: dict[str, set[str]] = {}
    for (a, b), _ in le.items():
        if a != b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set())
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    def strong(v: str):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                strong(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for v in sorted(adj):
        if v not in index:
            strong(v)
    for comp in sccs:
        cycle = _cycle_path(comp, adj)
        yield cycle, le[(cycle[0], cycle[1])][0]


def _cycle_path(comp: list[str], adj: dict[str, set[str]]) -> list[str]:
    """A concrete cycle through the strongly connected component, starting at its first node."""
    start = comp[0]
    inside = set(comp)
    parent: dict[str, str | None] = {start: None}
    queue = [start]
    while queue:
        v = queue.pop(0)
        for w in sorted(adj.get(v, ())):
            if w not in inside:
                continue
            if w == start:
                path = [v]
                while parent[path[-1]] is not None:
                    path.append(parent[path[-1]])  # type: ignore[arg-type]
                return list(reversed(path))
            if w not in parent:
                parent[w] = v
                queue.append(w)
    return comp


def is_acyclic(layer_names: list[str], matrix: dict[tuple[str, str], int]) -> bool:
    adj = {n: {b for (a, b), c in matrix.items() if a == n and b != n and c} for n in layer_names}
    state: dict[str, int] = {}

    def visit(n: str) -> bool:
        if state.get(n) == 1:
            return False
        if state.get(n) == 2:
            return True
        state[n] = 1
        ok = all(visit(m) for m in adj[n])
        state[n] = 2
        return ok

    return all(visit(n) for n in layer_names)


# ---------------------------------------------------------------- drafting rules from names

# Conventional meaning of layer names. Lower rank = further inside; a layer must not import a higher rank.
# Layers at the same rank are siblings and get no rule between them. Names not listed (core, common, utils,
# security, settings, the package's own top-level modules...) are cross-cutting and get no vocabulary rule.
RANKS: dict[str, int] = {}
for _rank, _names in enumerate(
    [
        ("domain", "entities"),
        ("models", "schemas", "dto", "dtos", "contracts", "wire"),
        ("repositories", "repository", "persistence", "storage", "clients", "gateways"),
        ("services", "service", "usecases", "use_cases", "application", "handlers", "features"),
        # Clean Architecture's outer ring: adapters and delivery mechanisms are siblings.
        ("data", "adapters", "infrastructure",
         "routers", "router", "controllers", "api", "endpoints", "views", "presentation", "ui", "cli",
         "jobs", "worker", "workers", "tasks", "webapi"),
        ("main", "entry", "bootstrap", "di", "composition"),
    ]
):
    for _n in _names:
        RANKS[_n] = _rank
UI_NAMES = {"routers", "router", "controllers", "api", "endpoints", "views", "presentation", "ui", "cli", "webapi"}
ADAPTER_NAMES = {"data", "adapters", "infrastructure", "repositories", "repository", "persistence"}
INNER_NAMES = {"domain", "entities", "usecases", "use_cases", "application"}
TEST_NAMES = {"tests", "test", "testing", "spec", "specs", "e2e", "__tests__"}
MIN_OBSERVED = 3  # edges needed before an observed one-way arrow becomes a drafted rule


def infer_rules(layer_names: list[str], matrix: dict[tuple[str, str], int]) -> tuple[dict[str, dict], list[str]]:
    """Propose rules from layer names and the observed import matrix.

    Returns ({layer: {"cannot_import": {other: "warning"}}}, notes). Every rule is a warning so the
    first scan reports instead of failing; the user promotes what they agree with.
    """
    rules: dict[str, dict] = {n: {} for n in layer_names}
    notes: list[str] = []
    ranked = {n: RANKS[n] for n in layer_names if n in RANKS}

    def forbid(a: str, b: str, why: str):
        rules[a].setdefault("cannot_import", {})[b] = "warning"
        notes.append(f"{a} cannot_import {b}  ({why})")

    for t in (n for n in layer_names if n in TEST_NAMES):
        for n in layer_names:
            if n not in TEST_NAMES:
                rules[n].setdefault("cannot_import", {})[t] = "warning"
        notes.append(f"every layer cannot_import {t}  (nothing but tests imports tests)")
    for a, ra in ranked.items():
        for b, rb in ranked.items():
            if rb > ra:
                forbid(a, b, f"'{a}' is conventionally inside '{b}'")
    # Clean Architecture signal: an inner layer is named, so the outer ring should talk through it.
    if any(n in INNER_NAMES for n in layer_names):
        for ui in (n for n in layer_names if n in UI_NAMES):
            for ad in (n for n in layer_names if n in ADAPTER_NAMES):
                forbid(ui, ad, f"Clean Architecture: '{ui}' should reach '{ad}' through the inner layers' interfaces")
    unranked = [n for n in layer_names if n not in ranked and n not in TEST_NAMES]
    for x in unranked:
        for y in layer_names:
            if x == y or y in TEST_NAMES:
                continue
            yx, xy = matrix.get((y, x), 0), matrix.get((x, y), 0)
            if yx >= MIN_OBSERVED and xy == 0:
                forbid(x, y, f"observed: {y} -> {x} x{yx}, never the reverse")
    return rules, notes
