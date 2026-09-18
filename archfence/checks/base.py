"""The Check contract: one vertical slice per architectural concern.

A check owns three things and nothing else needs to know about it:
  parse   - its block of the project config (``slices:``, ``contracts:`` ...)
  run     - the violations it finds in a resolved Graph
  propose - the draft it suggests when ``archfence init`` looks at an unknown tree
Register an instance in ``checks.CHECKS`` and the config loader, scanner and init pick it up.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.graph import Graph
from ..core.model import Violation


@dataclass
class ProposeContext:
    """What a check may look at when drafting a config for a tree it has never seen."""

    repo_root: Path
    project_root: Path
    layer_names: list[str]
    layer_globs: dict[str, str]
    matrix: dict[tuple[str, str], int]  # (src layer, dst layer) -> resolved import count


@dataclass
class Proposal:
    fragment: dict = field(default_factory=dict)  # merged into the project's YAML mapping
    notes: list[str] = field(default_factory=list)  # one human line per drafted rule


class Check(ABC):
    key: str  # config key and violation namespace
    config_keys: tuple[str, ...] = ()  # extra top-level project keys this check reads (besides its own `key`)

    @abstractmethod
    def parse(self, raw_project: dict, ctx: str, layer_names: set[str]) -> Any | None:
        """Return this check's config object, or None when the project does not enable it."""

    @abstractmethod
    def run(self, graph: Graph, cfg: Any) -> Iterable[Violation]: ...

    def propose(self, ctx: ProposeContext) -> Proposal | None:
        return None
