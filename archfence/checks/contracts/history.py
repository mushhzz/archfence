"""Contract-first as a discipline, not just an outcome: did the operation id reach the spec before the code?

One `git log -p` over the spec and one over the project root, parsed for added lines that introduce an
operation id. Commit timestamps are compared; a route whose id landed in code first is reported."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ...core.model import Route, Violation

_SPEC_OP = re.compile(r"""^\+.*\boperationId\b["']?\s*:\s*["']?([A-Za-z0-9_.\-]+)""")
_CODE_OP = re.compile(r"""^\+.*(?:operation_id\s*=\s*|\bName\s*=\s*)["']([A-Za-z0-9_.\-]+)["']""")
_COMMIT = re.compile(r"^commit ([0-9a-f]{40})$")


def _git(repo: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, errors="replace").stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def first_introductions(repo: Path, pathspec: str, pattern: re.Pattern) -> dict[str, tuple[int, str]]:
    """operation id -> (commit unix time, short sha) of the oldest commit whose diff adds a line matching pattern."""
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
        m = pattern.match(line)
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
    spec_first = first_introductions(repo, str(spec_path.relative_to(repo)), _SPEC_OP)
    code_first = first_introductions(repo, str(project_root.relative_to(repo)), _CODE_OP)
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
