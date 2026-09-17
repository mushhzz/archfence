# Contributing

archfence is laid out the way it asks other codebases to be: a small kernel, language adapters,
and one vertical slice per architectural check. `archfence scan` runs on this repository in CI
using the `archfence.yml` at the root, so the layout below is enforced, not aspirational.

```
archfence/
  core/          the kernel every check shares
    model.py       SourceFile, Import, Route, Edge, Violation    (imports nothing)
    globs.py       glob matching                                  (imports nothing)
    config.py      projects, layers, waivers, presets, severities; the port checks register into
    graph.py       walk a project, run extractors, resolve imports into a dependency graph
    report.py      text / JSON / SARIF
    baseline.py    known-violations file
  extractors/    one per language; each turns a file into provides + imports (+ routes)
  checks/        one directory or module per check; each owns its config, rules and init proposal
    base.py        the Check contract and ProposeContext
    layers.py      cannot_import / can_only_import / externals / cycles, and the naming vocabulary
    slices.py      vertical slices
    contracts/     API contract first: config, OpenAPI comparison, git history
  draft.py       `archfence init`: tree discovery, then asks every check what it proposes
  cli.py         argument parsing and output only
  presets/       shipped configs
tests/           mirrors the tree: core/, extractors/, checks/, plus test_draft.py and test_cli.py
```

Dependencies point inward: `cli -> draft -> checks -> core -> extractors -> model`. Core never
imports a check; `archfence/__init__.py` is the composition root that loads the checks, which
register their config parsers with core.

## Adding a language

1. Create `archfence/extractors/<lang>.py` with a subclass of `Extractor`. Implement `extract`
   to return a `SourceFile` whose `provides` are the logical names the file defines (a namespace,
   a module path, a file path) and whose `imports` are the logical names it references, in the same
   vocabulary. Use `prepare` if you need to see every path first (package manifests, aliases).
   If the language has route decorators or attributes, fill `routes` too.
2. Register it in `extractors/__init__.py` (`REGISTRY`, and `GRAMMARS` for `archfence warm`).
3. Add `tests/extractors/test_<lang>.py`. Resolution is tested end to end in `tests/checks/`.
4. If `archfence init` should discover projects for it, add root and layer discovery to `draft.py`.

## Adding a check

1. Create `archfence/checks/<name>.py` (or a package) with a subclass of `Check`:
   - `key`: its block in the project config and its violation namespace
   - `parse(raw_project, ctx, layer_names)`: return your config object or `None` when not enabled;
     use `core.config.severity`, `subjects`, `string_list` and raise `ConfigError` on bad input
   - `run(graph, cfg)`: yield `Violation`s; severity `info` never fails a build
   - `propose(ctx)`: optional; return a `Proposal` whose `fragment` merges into the drafted YAML
2. Add it to `CHECKS` in `checks/__init__.py`. Nothing else changes: config loading, scanning,
   `init`, reports and waivers pick it up.
3. Add `tests/checks/test_<name>.py`, and a line in the README's rule table.

## Running

```bash
pip install -e '.[dev]'
archfence warm
pytest -q
archfence scan          # on this repo
```
