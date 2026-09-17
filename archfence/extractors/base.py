"""Extractor protocol and shared tree-sitter helpers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path

from tree_sitter import Node, Parser
from tree_sitter_language_pack import get_parser

from ..core.model import SourceFile


@lru_cache(maxsize=None)
def parser_for(grammar: str) -> Parser:
    return get_parser(grammar)  # type: ignore[arg-type]


def text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def walk(node: Node, types: set[str]):
    """Depth-first yield of every descendant whose type is in ``types``."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in types:
            yield n
        stack.extend(reversed(n.children))


class Extractor(ABC):
    """One per language. Turns a file into a SourceFile with logical provides/imports."""

    language: str
    grammar: str
    extensions: tuple[str, ...]

    def __init__(self, root: Path, source_roots: list[str] | None = None):
        self.root = root
        self.source_roots = source_roots or []

    def wants(self, rel_path: str) -> bool:
        return rel_path.endswith(self.extensions)

    def parse(self, rel_path: str, source: bytes):
        return parser_for(self.grammar).parse(source)

    @abstractmethod
    def extract(self, rel_path: str, source: bytes) -> SourceFile: ...

    def prepare(self, rel_paths: list[str]) -> None:
        """Optional hook: see every file path before extraction (for crate/package discovery)."""
