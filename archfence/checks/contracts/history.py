"""Contract-first as a discipline, not just an outcome: did the operation id reach the spec before the code?

One `git log -p` over the spec and one over the project root, parsed for added lines that introduce an
operation id. Commit timestamps are compared; a route whose id landed in code first is reported."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ...core.model import Route, Violation

_SPEC_OP = re.compile(r"""^\+.*\boperationId\b["']?\s*:\s*["']?([A-Za-z0-9_.\-]+)""")
# An operation id introduced in code. Python/JS: `operation_id="..."` / `name="..."` on a route decorator.
# C#: the `Name = "..."` of an ASP.NET routing attribute -- required to sit on an Http<verb>/Route attribute
# line, so an ordinary `Name = "..."` property or object initialiser is not mistaken for a route id.
_CODE_OPS = (
    re.compile(r"""^\+.*\boperation_id\s*=\s*["']([A-Za-z0-9_.\-]+)["']"""),
    re.compile(r"""^\+.*\b(?:HttpGet|HttpPost|HttpPut|HttpPatch|HttpDelete|HttpHead|HttpOptions|Route)\b[^\n]*?\bName\s*=\s*["']([A-Za-z0-9_.\-]+)["']"""),
)
_COMMIT = re.compile(r"^commit ([0-9a-f]{40})$")


def _match_code_op(line: str):
    """First operation id a code diff line introduces, across the code-side patterns, or None."""
    for pattern in _CODE_OPS:
        m = pattern.match(line)
        if m:
            return m
    return None


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, errors="replace").stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def first_introductions(repo: Path, pathspec: str, match) -> dict[str, tuple[int, str]]:
    """operation id -> (commit unix time, short sha) of the oldest commit whose diff adds a matching line.

    ``match`` is a callable taking a diff line and returning a match (group(1) is the operation id) or None."""
    out = _git(repo, "log", "--reverse", "--format=commit %H %ct", "-p", "--no-color", "--", pathspec)
    if out is None:
        return {}
    first: dict[str, tuple[int, str]] = {}
    sha, ts = "", 0
    for line in out.splitlines():
        if line.startswith("commit ") and len(line.split()) == 3:
            _, sha, t = line.split()
            ts = int(t)
            continue
        m = match(line)
        if m and m.group(1) not in first:
            first[m.group(1)] = (ts, sha[:10])
    return first


def check_history(graph, cfg, code: list[tuple[str, Route]], spec: list[Route]):
    name = graph.project.name
    config_root = graph.config_root
    top = _git(config_root, "rev-parse", "--show-toplevel")
    if top is None:
        yield Violation("openapi-history", "contracts.openapi.history is on but this is not a git checkout", cfg.path, 1, None, None, "", name, "warning")
        return
    repo = Path(top.strip())
    spec_path = (config_root / cfg.path).resolve()
    project_root = (config_root / graph.project.root).resolve()
    spec_first = first_introductions(repo, str(spec_path.relative_to(repo)), _SPEC_OP.match)
    code_first = first_introductions(repo, str(project_root.relative_to(repo)), _match_code_op)
    spec_ops = {r.operation_id for r in spec if r.operation_id}
    for path, r in code:
        if not r.operation_id or r.operation_id not in spec_ops:
            continue  # drift is reported by the snapshot check
        c, s = code_first.get(r.operation_id), spec_first.get(r.operation_id)
        if c is None or s is None:
            continue  # uncommitted on one side: nothing to order yet
        if c[0] < s[0]:
            days = (s[0] - c[0]) / 86400
            yield Violation(
                "route-before-contract",
                f"'{r.operation_id}' reached code in {c[1]} {days:.0f} day(s) before it reached {cfg.path} in {s[1]}: the contract was written after the fact",
                path, r.line, graph.files[path].layer, None, r.operation_id, name, cfg.history_severity,
            )
