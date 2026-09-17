from pathlib import Path

from .base import Extractor
from .csharp import CSharpExtractor
from .dart import DartExtractor
from .python import PythonExtractor
from .rust import RustExtractor
from .typescript import TypeScriptExtractor

REGISTRY: dict[str, type[Extractor]] = {
    "csharp": CSharpExtractor,
    "cs": CSharpExtractor,
    "c#": CSharpExtractor,
    "dotnet": CSharpExtractor,
    "python": PythonExtractor,
    "py": PythonExtractor,
    "dart": DartExtractor,
    "flutter": DartExtractor,
    "rust": RustExtractor,
    "rs": RustExtractor,
    "typescript": TypeScriptExtractor,
    "ts": TypeScriptExtractor,
    "tsx": TypeScriptExtractor,
    "javascript": TypeScriptExtractor,
    "js": TypeScriptExtractor,
}

GRAMMARS = ("csharp", "python", "dart", "rust", "typescript", "tsx", "javascript")


def warm_grammars() -> list[str]:
    """Load every grammar once so a fresh machine or CI runner fetches them up front, not mid-scan."""
    from .base import parser_for

    for g in GRAMMARS:
        parser_for(g)
    return list(GRAMMARS)


def make_extractors(languages: list[str], root: Path, source_roots: list[str]) -> list[Extractor]:
    out: list[Extractor] = []
    seen: set[type[Extractor]] = set()
    for lang in languages:
        cls = REGISTRY.get(lang.lower())
        if cls is None:
            raise ValueError(f"unsupported language '{lang}'. Supported: csharp, python, dart, rust, typescript")
        if cls not in seen:
            seen.add(cls)
            out.append(cls(root, source_roots))
    return out


__all__ = ["Extractor", "REGISTRY", "make_extractors"]
