"""Scan a project, extract every file, and resolve logical imports to project files."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .config import ProjectConfig
from ..extractors import make_extractors
from .model import Edge, SourceFile

_SKIP_DIRS = {".git", "node_modules", "bin", "obj", "target", ".dart_tool", "build", "dist", "coverage", ".next", ".venv", "venv", "__pycache__", ".idea", ".vs"}


class EmptyProject(Exception):
    """The project's root and language matched no files: almost always a misconfiguration."""


def to_utf8(raw: bytes) -> bytes:
    """Normalise source bytes to UTF-8 for tree-sitter, which assumes UTF-8.

    UTF-8 (with or without a BOM) is passed through unchanged - the grammars tolerate the BOM. A UTF-16 or
    UTF-32 file (common for older Windows/VS C#) is decoded via its BOM; without this it is gibberish to the
    parser and the whole file fails to parse. Malformed content falls back to the raw bytes.
    """
    try:
        if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            return raw.decode("utf-32").encode("utf-8")
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16").encode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return raw
    return raw


@dataclass
class Graph:
    project: ProjectConfig
    config_root: Path = Path(".")
    files: dict[str, SourceFile] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    provides_index: dict[str, list[str]] = field(default_factory=dict)
    parse_errors: list[str] = field(default_factory=list)  # files the grammar could not fully parse
    read_errors: list[str] = field(default_factory=list)  # files that could not be read off disk

    def layer_edges(self) -> dict[tuple[str, str], list[Edge]]:
        """Resolved edges grouped by (src_layer, dst_layer), internal only."""
        out: dict[tuple[str, str], list[Edge]] = {}
        for e in self.edges:
            if e.dst is None:
                continue
            a, b = self.files[e.src].layer, self.files[e.dst].layer
            if a is None or b is None:
                continue
            out.setdefault((a, b), []).append(e)
        return out


def _iter_files(root: Path, project: ProjectConfig) -> list[str]:
    base = (root / project.root).resolve()
    rel_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = full.relative_to(base).as_posix()
            if project.is_ignored(rel):
                continue
            rel_paths.append(rel)
    return sorted(rel_paths)


def resolve_target(target: str, index: dict[str, list[str]], separators: tuple[str, ...] = ("::", "/", ".")) -> list[str]:
    """Longest-prefix lookup of a logical name in the provides index.

    Exact match first; otherwise strip trailing segments (so `using A.B.C` where a
    file only declares namespace `A.B` still resolves, and Python's
    `from pkg.mod import name` resolves to `pkg.mod`).
    """
    if target in index:
        return index[target]
    sep = next((s for s in separators if s in target), None)
    if sep is None:
        return []
    parts = target.split(sep)
    while len(parts) > 1:
        parts.pop()
        cand = sep.join(parts)
        if cand in index:
            return index[cand]
    return []


def build_graph(config_root: Path, project: ProjectConfig) -> Graph:
    root = (config_root / project.root).resolve()
    extractors = make_extractors(project.languages, root, project.source_roots)
    rel_paths = _iter_files(config_root, project)
    graph = Graph(project=project, config_root=config_root)

    for ex in extractors:
        wanted = [p for p in rel_paths if ex.wants(p)]
        ex.prepare(wanted)
        for rel in wanted:
            try:
                source = to_utf8((root / rel).read_bytes())
            except OSError:
                graph.read_errors.append(rel)
                continue
            sf = ex.extract(rel, source)
            sf.layer = project.layer_for(rel)
            graph.files[rel] = sf
            # Only surface a parse error when the file yielded nothing at all. tree-sitter recovers past most
            # errors (a C# `#if` block, say) with the imports/types/namespaces already captured, so warning
            # about those would be a false alarm; we warn only when extraction genuinely got nothing to analyse.
            if sf.parse_error and not (sf.imports or sf.provides or sf.types or sf.routes):
                graph.parse_errors.append(rel)
            for name in sf.provides:
                graph.provides_index.setdefault(name, []).append(rel)

    if not graph.files:
        raise EmptyProject(
            f"project '{project.name}': no {', '.join(project.languages)} files under '{(config_root / project.root)}' "
            f"(root is relative to the config file; check `root`, `languages` and `ignore`)"
        )

    seen: set[tuple[str, str | None, int]] = set()
    for sf in graph.files.values():
        for imp in sf.imports:
            targets = resolve_target(imp.target, graph.provides_index)
            if not targets:
                graph.edges.append(Edge(sf.path, None, imp.target, imp.line))
                continue
            if len(targets) > 1 and sf.scope is not None:
                same = [t for t in targets if graph.files[t].scope == sf.scope]
                if same:
                    targets = same  # three crates with one name: stay inside our own
            targets = [t for t in targets if t != sf.path]  # a self-reference (use super::* in mod tests) is not an edge
            if not targets:
                continue
            for t in targets:
                key = (sf.path, t, imp.line)
                if key in seen:
                    continue  # `from a.b import x, y` is one edge, not two
                seen.add(key)
                graph.edges.append(Edge(sf.path, t, imp.target, imp.line))
    return graph
