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


def count_members(type_node: Node, body_types: set[str], member_types: set[str]) -> int:
    """Count direct members of a class-like node: children of its body whose type is a member type.

    Counting only *direct* children keeps a nested type's methods out of its enclosing type's tally."""
    body = next((c for c in type_node.children if c.type in body_types), None)
    if body is None:
        return 0
    return sum(1 for c in body.children if c.type in member_types)


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
