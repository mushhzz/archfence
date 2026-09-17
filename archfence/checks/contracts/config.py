"""Configuration for the API-contract-first check."""
from __future__ import annotations

from dataclasses import dataclass, field

from ...core.config import ConfigError, severity, string_list

CONTRACT_LAYER_NAMES = ("schemas", "contracts", "dto", "dtos")
CONSUMER_LAYER_NAMES = ("routers", "router", "controllers", "api", "endpoints", "views", "webapi")


@dataclass
class OpenApiConfig:
    path: str  # spec file, relative to the config file
    prefix: str = ""  # mounted in front of every code route (FastAPI include_router(prefix=...))
    match: str = "operation_id"  # "operation_id" | "path" | "both"
    severity: str = "warning"  # code drifting from the contract
    pending: str = "info"  # contract entries with no code yet: expected in a contract-first flow
    history: bool = False  # git: the operation id must appear in the spec no later than in code
    history_severity: str = "warning"
    ignore: list[str] = field(default_factory=list)  # route path globs to skip, e.g. "/health*"


@dataclass
class ContractsConfig:
    """The contract layer is the source of truth and consumers speak only its types."""

    layer: str
    consumers: list[str] = field(default_factory=list)  # layers whose route-declaring files must import the contract layer
    allow: list[str] = field(default_factory=list)  # layers the contract layer itself may import (default: none)
    severity: str = "error"
    openapi: OpenApiConfig | None = None


def parse_contracts(raw, ctx: str, layer_names: set[str]) -> ContractsConfig | None:
    if not raw:
        return None
    if isinstance(raw, str):
        raw = {"layer": raw}
    layer = raw.get("layer")
    if not layer or str(layer) not in layer_names:
        raise ConfigError(f"{ctx}: contracts.layer must name one of the layers, got {layer!r}")
    consumers = string_list(raw.get("consumers"))
    for c in consumers:
        if c not in layer_names:
            raise ConfigError(f"{ctx}: contracts.consumers references unknown layer '{c}'")
    allow = string_list(raw.get("allow"))
    for a in allow:
        if a not in layer_names:
            raise ConfigError(f"{ctx}: contracts.allow references unknown layer '{a}'")
    openapi = None
    oa = raw.get("openapi")
    if oa:
        if isinstance(oa, str):
            oa = {"path": oa}
        if not oa.get("path"):
            raise ConfigError(f"{ctx}: contracts.openapi needs 'path'")
        match = str(oa.get("match", "operation_id"))
        if match not in ("operation_id", "path", "both"):
            raise ConfigError(f"{ctx}: contracts.openapi.match must be operation_id, path or both")
        openapi = OpenApiConfig(
            path=str(oa["path"]),
            prefix=str(oa.get("prefix") or ""),
            match=match,
            severity=severity(oa.get("severity"), f"{ctx} contracts.openapi.severity", "warning"),
            pending=severity(oa.get("pending"), f"{ctx} contracts.openapi.pending", "info"),
            history=bool(oa.get("history", False)),
            history_severity=severity(oa.get("history_severity"), f"{ctx} contracts.openapi.history_severity", "warning"),
            ignore=string_list(oa.get("ignore")),
        )
    return ContractsConfig(layer=str(layer), consumers=consumers, allow=allow, severity=severity(raw.get("severity"), f"{ctx} contracts.severity", "error"), openapi=openapi)
