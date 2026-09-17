"""`archfence init`: describe the architecture a tree already has before enforcing one.

Discovers projects and layers from the directory structure, prints the layer import matrix,
asks every registered check what it would propose, and writes a draft config whose rules are
all warnings. Language-specific discovery lives here; rule vocabulary lives with each check."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .checks import CHECKS, run_checks
from .checks.base import ProposeContext
from .core.config import ProjectConfig, parse_project
from .core.graph import _SKIP_DIRS, build_graph

_EXT = {"csharp": (".cs",), "python": (".py",), "dart": (".dart",), "rust": (".rs",), "typescript": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")}
_PY_ROOT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")


# ---------------------------------------------------------------- tree discovery


def _visible(p: Path, root: Path) -> bool:
    rel = p.relative_to(root).parts
    return not any(x in _SKIP_DIRS or x.startswith(".") for x in rel)


def _slug(name: str) -> str:
    # "Shop.Application" -> "application"; "remote_control" -> "remote_control"
    return name.rsplit(".", 1)[-1].lower().replace("-", "_")


def detect_languages(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            for lang, exts in _EXT.items():
                if fn.endswith(exts):
                    counts[lang] = counts.get(lang, 0) + 1
    return counts


def propose_layers(root: Path, language: str) -> list[tuple[str, str]]:
    """(layer name, glob) pairs guessed from the tree for C#, Dart or Rust."""
    exts = _EXT[language]
    if language == "csharp":
        projects = sorted({p.parent for p in root.rglob("*.csproj") if not any(s in p.parts for s in _SKIP_DIRS)})
        return [(_slug(p.name), f"{p.relative_to(root).as_posix()}/**") for p in projects if p != root]
    files = [p for p in root.rglob("*") if p.suffix in exts and _visible(p, root)]
    if not files:
        return []
    common = Path(os.path.commonpath([str(p.parent) for p in files]))
    if language == "dart" and (common / "lib").is_dir():
        common = common / "lib"
    if language == "rust" and (common / "src").is_dir():
        common = common / "src"
    groups: dict[str, str] = {}
    for f in files:
        if not f.is_relative_to(common):
            continue  # tests or tooling beside the source dir
        rel = f.relative_to(common)
        if len(rel.parts) > 1:
            groups[_slug(rel.parts[0])] = f"{(common / rel.parts[0]).relative_to(root).as_posix()}/**"
    if language == "rust" and not groups:
        groups["crate"] = f"{common.relative_to(root).as_posix()}/**"  # a flat crate: offer the crate itself
    return sorted(groups.items())


def python_roots(root: Path) -> list[Path]:
    """Directories that are their own Python source root: anything with a pyproject/setup file,
    plus the repo root for packages that live outside all of those."""
    roots = sorted({m.parent for marker in _PY_ROOT_MARKERS for m in root.rglob(marker) if _visible(m, root)})
    roots = [r for r in roots if not any(o != r and r.is_relative_to(o) for o in roots)]  # outermost only
    stray = [p for p in root.rglob("*.py") if _visible(p, root) and not any(p.is_relative_to(r) for r in roots)]
    if stray and root not in roots:
        roots.append(root)
    return roots


def propose_python_layers(src_root: Path, exclude: list[Path]) -> list[tuple[str, str]]:
    """A single top-level package is opened up one level (app/routers, app/services...);
    otherwise each top-level package or script dir is a layer."""
    files = [p for p in src_root.rglob("*.py") if _visible(p, src_root) and not any(p.is_relative_to(e) for e in exclude)]
    top: dict[str, list[Path]] = {}
    for f in files:
        rel = f.relative_to(src_root)
        if len(rel.parts) > 1:
            top.setdefault(rel.parts[0], []).append(f)
    packages = {d for d in top if (src_root / d / "__init__.py").exists()}
    main = [d for d in packages if d not in ("tests", "test", "scripts")]
    layers: dict[str, str] = {}
    if len(main) == 1 and len(top[main[0]]) >= 10:
        pkg = main[0]
        for f in top[pkg]:
            rel = f.relative_to(src_root / pkg)
            if len(rel.parts) > 1:
                layers[_slug(rel.parts[0])] = f"{pkg}/{rel.parts[0]}/**"
            else:
                layers[_slug(pkg)] = f"{pkg}/*.py"  # the package's own modules; subpackages are more specific and win
        for d in top:
            if d != pkg:
                layers[_slug(d)] = f"{d}/**"
    else:
        for d in top:
            layers[_slug(d)] = f"{d}/**"
    return sorted(layers.items())


def ts_roots(root: Path) -> list[Path]:
    """Every package.json directory (outside node_modules) with TypeScript/JavaScript source of its own."""
    roots = sorted({m.parent for m in root.rglob("package.json") if _visible(m, root)})
    out = []
    for r in roots:
        inner = [o for o in roots if o != r and o.is_relative_to(r)]
        own = [p for p in r.rglob("*") if p.suffix in _EXT["typescript"] and _visible(p, r) and not any(p.is_relative_to(i) for i in inner)]
        if own:
            out.append(r)
    return out


def propose_ts_layers(src_root: Path) -> list[tuple[str, str]]:
    """Top-level directories holding source are the layers; config files at the root stay unlayered."""
    layers: dict[str, str] = {}
    inner = [m.parent for m in src_root.rglob("package.json") if m.parent != src_root and _visible(m, src_root)]
    for f in src_root.rglob("*"):
        if f.suffix not in _EXT["typescript"] or not _visible(f, src_root) or any(f.is_relative_to(i) for i in inner):
            continue
        rel = f.relative_to(src_root)
        if len(rel.parts) > 1:
            layers[_slug(rel.parts[0])] = f"{rel.parts[0]}/**"
    return sorted(layers.items())


# ---------------------------------------------------------------- drafting


def draft_projects(root: Path) -> dict[str, dict]:
    """Project name -> YAML mapping with languages, root and layers, and no rules yet."""
    doc: dict[str, dict] = {}

    def add(name: str, lang: str, project_root: str, layers: list[tuple[str, str]], **extra):
        if not layers:
            return
        spec: dict = {"languages": [lang], "root": project_root, "no_cycles": False}
        spec.update({k: v for k, v in extra.items() if v})
        spec["layers"] = {n: {"paths": [g]} for n, g in layers}
        doc[name] = spec

    for lang, _ in sorted(detect_languages(root).items(), key=lambda kv: (-kv[1], kv[0])):
        if lang == "typescript":
            roots = ts_roots(root)
            for r in roots:
                rel = r.relative_to(root).as_posix() if r != root else "."
                name = "typescript" if len(roots) == 1 else _slug(r.name)
                add(rel.replace("/", "_") if name in doc else name, lang, rel, propose_ts_layers(r))
        elif lang == "python":
            roots = python_roots(root)
            for r in roots:
                others = [o for o in roots if o != r and o.is_relative_to(r)]
                rel = r.relative_to(root).as_posix() if r != root else "."
                name = "python" if len(roots) == 1 else _slug(r.name)
                add(rel.replace("/", "_") if name in doc else name, lang, rel, propose_python_layers(r, others), source_roots=["."], ignore=[f"{o.relative_to(r).as_posix()}/**" for o in others])
        else:
            add(lang, lang, ".", propose_layers(root, lang))
    return doc


def _merge_fragment(spec: dict, fragment: dict) -> None:
    for key, value in fragment.items():
        if key == "layers":
            for lname, rules in value.items():
                spec["layers"].setdefault(lname, {}).update(rules)
        else:
            spec[key] = value


def _matrix_lines(names: list[str], matrix: dict[tuple[str, str], int]) -> list[str]:
    width = max(len(n) for n in names) + 2
    lines = [" " * width + "".join(f"{n[:10]:>11}" for n in names)]
    for a in names:
        lines.append(f"{a:<{width}}" + "".join(f"{matrix.get((a, b), 0) or '.':>11}" for b in names))
    return lines


def run(root: Path, out: Path | None, force: bool) -> tuple[str, str | None]:
    doc = draft_projects(root)
    if not doc:
        return f"archfence init: no {', '.join(_EXT)} files found under {root}", None
    lines: list[str] = []
    for name, spec in doc.items():
        project = parse_project(dict(spec, name=name), name)
        g = build_graph(root, project)
        names = project.layer_names()
        matrix = {k: len(v) for k, v in g.layer_edges().items()}
        lines.append(f"[{name}] {len(g.files)} files, {sum(1 for f in g.files.values() if f.layer)} in proposed layers")
        lines += _matrix_lines(names, matrix)

        ctx = ProposeContext(root, root / spec["root"], names, {n: l["paths"][0] for n, l in spec["layers"].items()}, matrix)
        notes: list[str] = []
        for check in CHECKS:
            proposal = check.propose(ctx)
            if proposal is not None:
                _merge_fragment(spec, proposal.fragment)
                notes += proposal.notes
        lines.append("")
        lines.append(f"  proposed rules ({len(notes)}), all as warnings:")
        lines += [f"    {n}" for n in notes]

        project = parse_project(dict(spec, name=name), name)  # re-parse with the drafted rules
        g.project = project
        for f in g.files.values():
            f.layer = project.layer_for(f.path)
        violations, _ = run_checks(g)
        lines.append(f"  these rules flag {len(violations)} import(s) in the code today" + (":" if violations else ""))
        for v in sorted(violations, key=lambda v: (v.path, v.line))[:15]:
            lines.append(f"    {v.path}:{v.line}: {v.message}")
        if len(violations) > 15:
            lines.append(f"    ... {len(violations) - 15} more; run `archfence scan` for the full list")
        lines.append("")
    lines.append("Rows import columns. Rules were drafted from what the layer names conventionally mean and from arrows the code")
    lines.append("only ever draws one way. Delete the ones you disagree with, change `warning` to `error` for the ones that should")
    lines.append("fail a build, and `archfence baseline` records today's leftovers so only new breaches fail.")
    written = None
    if out is not None:
        if out.exists() and not force:
            lines.append(f"\n{out} already exists; pass --force to overwrite it.")
        else:
            header = (
                "# Drafted by `archfence init`. Layers come from the directory tree; rules from what the layer names conventionally\n"
                "# mean and from import directions the code already respects. Every rule is a warning until you promote it.\n"
            )
            out.write_text(header + yaml.safe_dump({"projects": doc}, sort_keys=False), encoding="utf-8")
            written = str(out)
    return "\n".join(lines), written
