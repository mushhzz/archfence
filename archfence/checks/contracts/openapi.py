"""Reading an OpenAPI document and comparing it with the routes found in code.

The comparison is asymmetric on purpose: contract first means the spec leads, so a route the spec
does not know is drift, while a spec entry the code has not implemented yet is pending work."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Iterable

import yaml

from ...core.graph import Graph
from ...core.model import Route, Violation
from .config import OpenApiConfig

_PARAM = re.compile(r"\{[^}]*\}|<[^>]*>|:[A-Za-z_][A-Za-z0-9_]*")
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")


def normalise_path(path: str) -> str:
    """`/users/{user_id}`, `/users/<int:id>` and `/users/:id` all become `/users/{}`; trailing slash dropped."""
    p = _PARAM.sub("{}", path)
    p = re.sub(r"/+", "/", "/" + p.strip("/"))
    return p if p == "/" else p.rstrip("/")


def load_openapi_doc(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return (json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)) or {}


def spec_routes(doc: dict) -> list[Route]:
    routes: list[Route] = []
    for p, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in _METHODS and isinstance(op, dict):
                routes.append(Route(method.upper(), str(p), 0, op.get("operationId"), ""))
    return routes


def generated_spec_telltales(doc: dict) -> list[str]:
    """Signs that the checked-in document was dumped from the framework rather than authored."""
    tells = []
    info = doc.get("info") or {}
    if str(info.get("title", "")).strip() in ("FastAPI", "Flask API", "API", "Swagger Petstore"):
        tells.append(f"info.title is the framework default '{info.get('title')}'")
    schemas = (doc.get("components") or {}).get("schemas") or {}
    if "HTTPValidationError" in schemas and "ValidationError" in schemas:
        tells.append("components.schemas carries FastAPI's HTTPValidationError/ValidationError pair")
    if any(k.startswith("Body_") for k in schemas):
        tells.append("components.schemas has FastAPI's synthesised Body_* models")
    return tells


def guess_prefix(spec_path: Path) -> str:
    """The path prefix every operation shares, e.g. `/api/v1`; used by `init` to guess the mount point."""
    try:
        doc = load_openapi_doc(spec_path)
    except Exception:
        return ""
    paths = [p for p in (doc.get("paths") or {}) if isinstance(p, str)]
    if not paths:
        return ""
    segs = [p.strip("/").split("/") for p in paths]
    prefix: list[str] = []
    for parts in zip(*segs):
        if len(set(parts)) == 1 and not parts[0].startswith("{"):
            prefix.append(parts[0])
        else:
            break
    if any(len(s) <= len(prefix) for s in segs):  # a prefix that swallows a whole path is not a mount point
        prefix = prefix[:-1]
    return "/" + "/".join(prefix) if prefix else ""


def code_routes(graph: Graph, cfg: OpenApiConfig, consumers: list[str]) -> list[tuple[str, Route]]:
    """Routes declared in consumer files, with the mount prefix applied and paths normalised."""
    out: list[tuple[str, Route]] = []
    for path, f in graph.files.items():
        if consumers and f.layer not in consumers:
            continue
        for r in f.routes:
            if r.method == "WEBSOCKET":
                continue
            full = normalise_path(cfg.prefix + r.path)
            if any(fnmatch.fnmatchcase(full, g) or fnmatch.fnmatchcase(r.path, g) for g in cfg.ignore):
                continue
            out.append((path, Route(r.method, full, r.line, r.operation_id, r.handler)))
    return out


def compare(graph: Graph, cfg: OpenApiConfig, consumers: list[str]) -> Iterable[Violation]:
    name = graph.project.name
    spec_path = Path(cfg.path) if Path(cfg.path).is_absolute() else graph.config_root / cfg.path
    if not spec_path.exists():
        yield Violation("openapi-missing", f"OpenAPI document not found: {cfg.path}", cfg.path, 1, None, None, "", name, "error")
        return
    doc = load_openapi_doc(spec_path)
    tells = generated_spec_telltales(doc)
    if tells:
        yield Violation("openapi-generated", f"{cfg.path} looks generated from the code, not authored first: " + "; ".join(tells), cfg.path, 1, None, None, "", name, "warning")

    spec = [r for r in spec_routes(doc) if not any(fnmatch.fnmatchcase(normalise_path(r.path), g) for g in cfg.ignore)]
    code = code_routes(graph, cfg, consumers)
    by_op = {r.operation_id: r for r in spec if r.operation_id}
    by_path: dict[tuple[str, str], Route] = {}
    for r in spec:
        by_path.setdefault((normalise_path(r.path), r.method), r)
    matched: set[int] = set()

    def find(route: Route):
        if cfg.match in ("operation_id", "both") and route.operation_id and route.operation_id in by_op:
            return by_op[route.operation_id], "operation_id"
        if cfg.match in ("path", "both") or not route.operation_id:
            hit = by_path.get((route.path, route.method))
            if hit is None and route.method == "*":
                hit = next((v for (p, _m), v in by_path.items() if p == route.path), None)
            if hit is not None:
                return hit, "path"
        return None, None

    for path, r in code:
        hit, how = find(r)
        if hit is None:
            what = f"operation_id '{r.operation_id}'" if r.operation_id and cfg.match != "path" else f"{r.method} {r.path}"
            yield Violation("route-not-in-contract", f"{r.method} {r.path} ({r.handler}) is not in {cfg.path}: no {what}", path, r.line, graph.files[path].layer, None, r.path, name, cfg.severity)
            continue
        matched.add(id(hit))
        if how == "operation_id":
            spec_p = normalise_path(hit.path)
            if spec_p != r.path or hit.method != r.method:
                yield Violation("route-contract-mismatch", f"operation '{r.operation_id}' is {r.method} {r.path} in code but {hit.method} {spec_p} in {cfg.path}", path, r.line, graph.files[path].layer, None, r.path, name, cfg.severity)
    for r in spec:
        if id(r) not in matched:
            label = f"{r.method} {r.path}" + (f" ({r.operation_id})" if r.operation_id else "")
            yield Violation("contract-pending", f"{label} is in {cfg.path} and not implemented yet", cfg.path, 1, None, None, r.path, name, cfg.pending)

    if cfg.history:
        from .history import check_history

        yield from check_history(graph, cfg, code, spec)
