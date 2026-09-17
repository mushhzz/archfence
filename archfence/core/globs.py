"""Path globbing shared by every layer, slice and waiver definition."""
from __future__ import annotations

import re
from functools import lru_cache


def expand_braces(pattern: str) -> list[str]:
    """Expand shell-style ``{a,b}`` alternatives into a list of plain globs."""
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(expand_braces(pattern[: m.start()] + alt.strip() + pattern[m.end():]))
    return out


@lru_cache(maxsize=4096)
def _glob_re(pattern: str) -> re.Pattern:
    """Translate a glob to a regex: `*` and `?` never cross `/`, `**` spans directories,
    `[...]` classes pass through, a trailing `/` means everything underneath."""
    if pattern.endswith("/"):
        pattern += "**"
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                    out.append("(?:.*/)?")  # `**/` matches zero or more directories
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pattern.find("]", i)
            if j == -1:
                out.append(re.escape(c))
            else:
                out.append(pattern[i : j + 1])
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(rel_path: str, pattern: str) -> bool:
    """Glob match where `*` stays within one path segment, `**` spans directories, `{a,b}` alternates,
    and a directory prefix (`x/`) matches everything under it."""
    if "{" in pattern:
        return any(glob_match(rel_path, p) for p in expand_braces(pattern))
    return _glob_re(pattern).match(rel_path) is not None


def specificity(rel_path: str, patterns: list[str]) -> tuple[int, int] | None:
    """How precisely the best matching pattern pins the path: (literal segments, total segments)."""
    best = None
    for pattern in patterns:
        for pat in expand_braces(pattern):
            if not glob_match(rel_path, pat):
                continue
            segs = [x for x in pat.rstrip("/").split("/") if x]
            literal = sum(1 for x in segs if not any(ch in x for ch in "*?["))
            score = (literal, len(segs))
            if best is None or score > best:
                best = score
    return best
