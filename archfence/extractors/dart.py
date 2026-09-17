"""Dart: provides = file path and package: URI; imports/exports/parts resolved to the same vocabulary."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

from ..core.model import Import, SourceFile
from .base import Extractor, text, walk

_URI_RE = re.compile(r"""^['"](.*?)['"]$""")


class DartExtractor(Extractor):
    language = "dart"
    grammar = "dart"
    extensions = (".dart",)

    def __init__(self, root: Path, source_roots=None):
        super().__init__(root, source_roots)
        self.packages: dict[str, str] = {}  # package name -> lib dir (relative posix)

    def prepare(self, rel_paths: list[str]) -> None:
        seen: set[str] = set()
        for rp in rel_paths:
            d = str(PurePosixPath(rp).parent)
            while d not in seen:
                seen.add(d)
                pubspec = self.root / d / "pubspec.yaml"
                if pubspec.exists():
                    try:
                        name = (yaml.safe_load(pubspec.read_text(encoding="utf-8")) or {}).get("name")
                    except yaml.YAMLError:
                        name = None
                    if name:
                        lib = str(PurePosixPath(d) / "lib") if d != "." else "lib"
                        self.packages[str(name)] = lib
                    break
                if d in (".", ""):
                    break
                d = str(PurePosixPath(d).parent)

    def _provides(self, rel_path: str) -> list[str]:
        out = [rel_path]
        for name, lib in self.packages.items():
            prefix = lib.rstrip("/") + "/"
            if rel_path.startswith(prefix):
                out.append(f"package:{name}/{rel_path[len(prefix):]}")
        return out

    def _resolve(self, uri: str, rel_path: str) -> str:
        if uri.startswith(("package:", "dart:")):
            # Map package: URIs for local packages onto file paths so they resolve.
            if uri.startswith("package:"):
                pkg, _, rest = uri[len("package:"):].partition("/")
                if pkg in self.packages:
                    return str(PurePosixPath(self.packages[pkg]) / rest)
            return uri
        base = PurePosixPath(rel_path).parent
        joined = PurePosixPath(*(base / uri).parts)
        parts: list[str] = []
        for part in joined.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part not in (".", ""):
                parts.append(part)
        return "/".join(parts)

    def extract(self, rel_path: str, source: bytes) -> SourceFile:
        tree = self.parse(rel_path, source)
        sf = SourceFile(path=rel_path, language=self.language)
        sf.provides = self._provides(rel_path)
        for node in walk(tree.root_node, {"import_or_export", "part_directive"}):
            line = node.start_point[0] + 1
            raw = text(node).strip()
            uri_node = next(iter(walk(node, {"string_literal"})), None)
            if uri_node is None:
                continue
            m = _URI_RE.match(text(uri_node).strip())
            if not m:
                continue
            uri = m.group(1)
            sf.imports.append(Import(self._resolve(uri, rel_path), line, raw))
        return sf
