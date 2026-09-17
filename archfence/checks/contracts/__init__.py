"""API contract first: the contract layer is the source of truth, consumers speak its types,
and the checked-in OpenAPI document agrees with the code (and, optionally, preceded it in git)."""
from __future__ import annotations

from typing import Iterable

from ...core.graph import Graph
from ...core.model import Violation
from ..base import Check, Proposal, ProposeContext
from .config import CONSUMER_LAYER_NAMES, CONTRACT_LAYER_NAMES, ContractsConfig, OpenApiConfig, parse_contracts
from .openapi import compare, guess_prefix, normalise_path

_SPEC_FILES = ("openapi.yaml", "openapi.yml", "openapi.json", "swagger.yaml", "swagger.json")


class ContractsCheck(Check):
    key = "contracts"

    def parse(self, raw_project: dict, ctx: str, layer_names: set[str]) -> ContractsConfig | None:
        return parse_contracts(raw_project.get("contracts"), ctx, layer_names)

    def run(self, graph: Graph, cfg: ContractsConfig) -> Iterable[Violation]:
        name = graph.project.name
        contract_files = {p for p, f in graph.files.items() if f.layer == cfg.layer}

        # 1. The contract layer depends on nothing but what `allow` names.
        for edge in graph.edges:
            if edge.src in contract_files and edge.dst is not None:
                dst_layer = graph.files[edge.dst].layer
                if dst_layer not in (None, cfg.layer) and dst_layer not in cfg.allow:
                    yield Violation("contract-depends-on-code", f"contract layer '{cfg.layer}' must not depend on layer '{dst_layer}' ({edge.target}); contracts are the source of truth", edge.src, edge.line, cfg.layer, dst_layer, edge.target, name, cfg.severity)

        # 2. Every route-declaring file in a consumer layer speaks contract types.
        imports_contract = {e.src for e in graph.edges if e.dst in contract_files}
        for path, f in graph.files.items():
            if f.layer in cfg.consumers and f.routes and path not in imports_contract:
                first = min(f.routes, key=lambda r: r.line)
                yield Violation("route-without-contract", f"{len(f.routes)} route(s) declared but nothing imported from contract layer '{cfg.layer}'", path, first.line, f.layer, cfg.layer, "", name, cfg.severity)

        # 3. Optional: the checked-in OpenAPI document and the code agree.
        if cfg.openapi is not None:
            yield from compare(graph, cfg.openapi, cfg.consumers)

    def propose(self, ctx: ProposeContext) -> Proposal | None:
        layer = next((n for n in CONTRACT_LAYER_NAMES if n in ctx.layer_names), None)
        if layer is None:
            return None
        consumers = [n for n in ctx.layer_names if n in CONSUMER_LAYER_NAMES]
        spec: dict = {"layer": layer, "consumers": consumers, "severity": "warning"}
        if "domain" in ctx.layer_names:
            spec["allow"] = ["domain"]
        note = f"contract layer '{layer}' imports nothing" + (f"; {', '.join(consumers)} must import it" if consumers else "")
        found = sorted(
            p for pat in _SPEC_FILES for p in ctx.repo_root.rglob(pat)
            if not any(x.startswith(".") or x == "node_modules" for x in p.relative_to(ctx.repo_root).parts)
        )
        if found:
            chosen = found[0]
            oa: dict = {"path": chosen.relative_to(ctx.repo_root).as_posix(), "match": "both", "severity": "warning"}
            prefix = guess_prefix(chosen)
            if prefix:
                oa["prefix"] = prefix
            spec["openapi"] = oa
            note += f"; routes compared with {oa['path']}" + (f" under {prefix}" if prefix else "")
        return Proposal({"contracts": spec}, [note])


__all__ = ["ContractsCheck", "ContractsConfig", "OpenApiConfig", "normalise_path"]
