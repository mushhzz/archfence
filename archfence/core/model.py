"""Language-neutral data model shared by extractors, resolver and rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class Import:
    """One dependency edge as written in source, before resolution.

    ``target`` is a logical name in the language's own vocabulary
    (a C# namespace, a Python dotted module, a Dart URI, a Rust path).
    Extractors normalise relative references into absolute logical names
    so the core never needs language knowledge.
    """

    target: str
    line: int
    raw: str = ""


@dataclass(frozen=True)
class Route:
    """An HTTP endpoint declared in source (a FastAPI/Flask decorator, an ASP.NET attribute)."""

    method: str  # upper-case, or "*" when the framework decides
    path: str  # as declared, including any router/class prefix visible in the same file
    line: int
    operation_id: str | None = None
    handler: str = ""


@dataclass
class SourceFile:
    """A parsed source file: what it provides and what it imports."""

    path: str  # posix path relative to the project root
    language: str
    provides: list[str] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    layer: str | None = None
    scope: str | None = None  # crate / package the file belongs to; resolution prefers the same scope
    routes: list[Route] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name


@dataclass(frozen=True)
class Edge:
    """A resolved dependency between two files (or a file and an external)."""

    src: str
    dst: str | None  # None when the import did not resolve to a project file
    target: str  # the logical name as imported
    line: int


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str
    path: str
    line: int
    src_layer: str | None
    dst_layer: str | None
    target: str
    project: str
    severity: str = "error"
