"""archfence command line."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .checks import run_checks
from .core import baseline as bl
from .core.config import ConfigError, load_config
from .core.graph import EmptyProject, build_graph
from .core.report import json_report, sarif_report, text_report


def _find_config(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for name in ("archfence.yml", "archfence.yaml", ".archfence.yml"):
        p = Path.cwd() / name
        if p.exists():
            return p
    raise ConfigError("no archfence.yml found in the current directory (use --config)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="archfence", description="Polyglot architecture linter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="check the codebase against the configured layer rules")
    scan.add_argument("-c", "--config", help="path to archfence.yml (default: ./archfence.yml)")
    scan.add_argument("-f", "--format", choices=["text", "json", "sarif"], default="text")
    scan.add_argument("-o", "--output", help="write the report to this file instead of stdout")
    scan.add_argument("-p", "--project", action="append", help="only run these project(s) from the config")
    scan.add_argument("-v", "--verbose", action="store_true", help="show the layer dependency matrix")
    scan.add_argument("--warn-only", action="store_true", help="exit 0 even when violations are found")
    scan.add_argument("--baseline", help=f"ignore violations recorded in this file (default: ./{bl.DEFAULT_NAME} if present)")
    scan.add_argument("--no-baseline", action="store_true", help="report everything, even baselined violations")
    scan.add_argument("--show-waived", action="store_true", help="also list violations covered by an `allow` waiver")

    base = sub.add_parser("baseline", help="record the current violations so that only new ones fail future scans")
    base.add_argument("-c", "--config")
    base.add_argument("-o", "--output", default=bl.DEFAULT_NAME)

    init = sub.add_parser("init", help="draft archfence.yml: layers from the tree, rules from their names, and what those rules flag today")
    init.add_argument("path", nargs="?", default=".", help="repository root to inspect")
    init.add_argument("-o", "--output", default="archfence.yml", help="where to write the draft config ('-' to skip)")
    init.add_argument("--force", action="store_true", help="overwrite an existing config")

    deps = sub.add_parser("deps", help="print the resolved layer-to-layer dependency counts")
    deps.add_argument("-c", "--config")
    deps.add_argument("-p", "--project", action="append")
    deps.add_argument("--files", action="store_true", help="list every cross-layer edge with file locations")

    unres = sub.add_parser("unresolved", help="list imports that did not resolve to a project file (external deps)")
    unres.add_argument("-c", "--config")
    unres.add_argument("-p", "--project", action="append")
    unres.add_argument("--top", type=int, default=40)

    routes_p = sub.add_parser("routes", help="list the HTTP routes found in source (what the contract checks compare against the spec)")
    routes_p.add_argument("-c", "--config")
    routes_p.add_argument("-p", "--project", action="append")

    sub.add_parser("warm", help="download and load every tree-sitter grammar now (for fresh machines and CI caches)")

    args = ap.parse_args(argv)
    if args.cmd == "warm":
        from .extractors import warm_grammars

        print("archfence: grammars ready: " + ", ".join(warm_grammars()))
        return 0
    if args.cmd == "init":
        from .draft import run as init_run

        out = None if args.output == "-" else Path(args.output)
        report, written = init_run(Path(args.path).resolve(), out, args.force)
        print(report)
        if written:
            print(f"\narchfence: wrote {written}")
        return 0
    try:
        config = load_config(_find_config(args.config))
    except ConfigError as e:
        print(f"archfence: config error: {e}", file=sys.stderr)
        return 2

    projects = config.projects
    if getattr(args, "project", None):
        projects = [p for p in projects if p.name in set(args.project)]
        if not projects:
            print(f"archfence: no project named {args.project}", file=sys.stderr)
            return 2

    try:
        graphs = [build_graph(config.path.parent, p) for p in projects]
    except EmptyProject as e:
        print(f"archfence: {e}", file=sys.stderr)
        return 2
    except ValueError as e:  # unsupported language
        print(f"archfence: config error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "deps":
        for g in graphs:
            print(f"[{g.project.name}]")
            for (a, b), edges in sorted(g.layer_edges().items()):
                if a == b:
                    continue
                print(f"  {a:<16} -> {b:<16} {len(edges)}")
                if args.files:
                    for e in sorted(edges, key=lambda e: (e.src, e.line)):
                        print(f"      {e.src}:{e.line}  ->  {e.dst}")
        return 0

    if args.cmd == "routes":
        for g in graphs:
            contracts = g.project.check("contracts")
            prefix = contracts.openapi.prefix if contracts is not None and contracts.openapi is not None else ""
            rows = [(f.path, r) for f in g.files.values() for r in f.routes]
            print(f"[{g.project.name}] {len(rows)} route(s)" + (f", prefix {prefix}" if prefix else ""))
            for path, r in sorted(rows, key=lambda x: (x[1].path, x[1].method)):
                op = f"  op={r.operation_id}" if r.operation_id else ""
                print(f"  {r.method:<8} {prefix + r.path:<50} {path}:{r.line}{op}")
        return 0

    if args.cmd == "unresolved":
        for g in graphs:
            counts: dict[str, int] = {}
            for e in g.edges:
                if e.dst is None:
                    counts[e.target] = counts.get(e.target, 0) + 1
            print(f"[{g.project.name}] {len(counts)} distinct unresolved targets")
            for t, n in sorted(counts.items(), key=lambda kv: -kv[1])[: args.top]:
                print(f"  {n:>5}  {t}")
        return 0

    violations: list = []
    waived: list = []
    for g in graphs:
        kept, w = run_checks(g)
        violations.extend(kept)
        waived.extend(w)

    if args.cmd == "baseline":
        n = bl.write(Path(args.output), violations)
        print(f"archfence: recorded {n} violation(s) in {args.output}; future scans only fail on new ones")
        return 0

    baselined: list = []
    if not args.no_baseline:
        bpath = Path(args.baseline) if args.baseline else config.path.parent / bl.DEFAULT_NAME
        if bpath.exists():
            violations, baselined = bl.split(violations, bl.load(bpath))
        elif args.baseline:
            print(f"archfence: baseline file not found: {bpath}", file=sys.stderr)
            return 2

    if args.format == "json":
        out = json_report(violations, graphs, baselined=baselined, waived=waived)
    elif args.format == "sarif":
        out = sarif_report(violations)
    else:
        out = text_report(violations, graphs, verbose=args.verbose, baselined=baselined, waived=waived, show_waived=args.show_waived)
    if args.output:
        Path(args.output).write_text(out + "\n", encoding="utf-8")
        print(f"archfence: wrote {args.output} ({len(violations)} violation(s))")
    else:
        print(out)
    errors = [v for v in violations if v.severity == "error"]
    return 0 if (not errors or args.warn_only) else 1


if __name__ == "__main__":
    sys.exit(main())
