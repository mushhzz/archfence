# archfence

A polyglot architecture linter. One YAML file describes your layers and which layer may
depend on which; archfence parses the code with tree-sitter and fails the build when an
import crosses a line it should not.

Checks: layer dependency rules, external-package bans, layer cycles, vertical slices, and
API-contract-first (contract layer purity, routers speak contract types, OpenAPI drift).
Supported today: **C#**, **Python**, **TypeScript/JavaScript**, **Dart/Flutter**, **Rust**. Adding a language means
writing one extractor (about 80 lines) that reports what a file *provides* and what it
*imports* in that language's own vocabulary. Everything else is shared.

## Install

```bash
pip install git+https://github.com/mushhzz/archfence@v0.1.0
archfence warm               # fetch the tree-sitter grammars once (also what CI should cache)
archfence init               # draft archfence.yml from the tree
archfence scan               # check it
```

Grammars come from `tree-sitter-language-pack`, which downloads each one on first use into
`~/.cache/tree-sitter-language-pack`. Run `archfence warm` on a fresh machine or as a cached CI
step so the first scan does not need the network.

### CI and pre-commit

`examples/github-action.yml` is a workflow that fails on errors and uploads SARIF so findings
show up on the pull request. `examples/pre-commit-config.yml` wires the hook; this repository
ships `.pre-commit-hooks.yaml`, so `repo: https://github.com/mushhzz/archfence` with `id: archfence`
is all a project needs.

A project whose `root` and `languages` match no files is a configuration error (exit 2), not a
clean scan.

## Configure

```yaml
projects:
  backend:
    extends: clean-architecture-dotnet     # optional preset; you override paths
    layers:
      domain:         { paths: ["src/MyApp.Domain/**"] }
      application:    { paths: ["src/MyApp.Application/**"] }
      infrastructure: { paths: ["src/MyApp.Infrastructure/**"] }
      webapi:         { paths: ["src/MyApp.WebApi/**"] }
    ignore: ["**/Migrations/**"]

  web:
    languages: [dart]
    root: web
    layers:
      domain:       { paths: ["lib/domain/**"], can_only_import: [],
                      cannot_import_external: ["package:flutter/", "dart:io"] }
      data:         { paths: ["lib/data/**"], can_only_import: [domain, core] }
      presentation: { paths: ["lib/presentation/**"], cannot_import: [data] }
      core:         { paths: ["lib/core/**"] }
```

Per layer:

| key | meaning |
|---|---|
| `paths` | globs, relative to the project `root`. `**`, `*` and `{a,b}` work. The most specific matching layer wins. |
| `cannot_import` | layers this layer must not depend on |
| `can_only_import` | the complete allow-list of layers (an empty list means "nothing but itself") |
| `cannot_import_external` | prefixes of third-party names that are forbidden here (`Microsoft.EntityFrameworkCore`, `package:flutter/`, `sqlalchemy`, `tokio::`) |
| `can_only_import_external` | allow-list version of the above |

Per project: `languages`, `root`, `ignore`, `source_roots` (Python only), `no_cycles`
(`error`, `warning` or `off`; default `error`), `strict` (promote every warning to an error),
`allow` (waivers, below) and `extends` (a preset from `archfence/presets/`:
`clean-architecture-dotnet`, `flutter-clean-bloc`, `python-layered`, `rust-hexagonal`).

### Severity

Every rule has a severity. **Errors fail the build; warnings are reported and exit 0.**

```yaml
layers:
  domain:
    paths: ["src/*.Domain/**"]
    can_only_import: []                         # error (the layer default)
    cannot_import_external:
      Microsoft.EntityFrameworkCore: error      # per-rule severity
      System.Text.Json: warning
  services:
    paths: ["src/services/**"]
    severity: warning                           # default for every rule on this layer
    cannot_import: {adapters: error}            # ...which a single rule can override
```

The shipped presets treat layer-to-layer rules (the Dependency Rule itself) as errors and
framework bans as warnings, because strict Clean Architecture forbids them but most teams
tolerate some. Set `strict: true` on a project to make the purist reading the enforced one.

### Waivers

Known, accepted exceptions are declared with a reason. The reason is mandatory so the
exception documents itself.

```yaml
allow:
  - path: "lib/presentation/legacy/**"
    layers: [data]
    reason: "pre-BLoC screens, tracked in ARCH-12"
  - path: "src/App.Domain/Json/**"
    external: [System.Text.Json]
    reason: "converters that the enum attributes need"
  - path: "lib/core/router/**"
    rules: [layer-cycle]
    reason: "go_router: the router names screens and screens navigate via the router"
```

`layers`, `external` and `rules` each narrow the waiver; omit all three to waive everything
under `path`. `archfence scan --show-waived` lists what a waiver is currently hiding.

### Vertical slices

```yaml
slices:
  roots: ["app/features/*"]        # each child directory is a slice
  shared: ["app/core/**"]          # code every slice may use
  shared_only: false               # true: a slice may import nothing outside itself and `shared`
  allow: { reports: [users] }      # explicit exceptions, slice -> slices it may import
  severity: error
  shared_imports_slices: error     # shared code depending on a slice
```

Rules: `slice-coupling` (a slice imports a sibling), `shared-imports-slice`, and with
`shared_only`, `slice-escape`. Slices coexist with layers; both are checked.

### API contract first

```yaml
contracts:
  layer: schemas                   # the contract layer; it may import nothing (or only `allow`)
  consumers: [routers]             # every route-declaring file here must import contract types
  allow: [domain]
  openapi:
    path: docs/contracts/openapi.yaml
    prefix: /api/v1                # mounted in front of every code route
    match: both                    # operation_id | path | both
    ignore: ["/health*"]
    severity: warning
```

Routes are read from source: FastAPI, Starlette and Flask decorators in Python, including a
module-level `APIRouter(prefix=...)`, and ASP.NET `[Route]` / `[HttpGet]` attributes in C#.
`archfence routes` lists what was found. Path parameters are normalised, so `{user_id}`, `{id}`,
`<int:id>` and `:id` all agree.

The comparison is deliberately asymmetric, because contract first means the spec leads:

| rule | meaning | default |
|---|---|---|
| `route-not-in-contract` | code declares a route the spec does not know | `severity` (warning) |
| `route-contract-mismatch` | same operation id, different method or path | `severity` |
| `contract-pending` | the spec has it, the code does not yet: expected between agreeing and implementing | `pending` (info) |
| `openapi-generated` | the spec carries framework telltales (FastAPI's `HTTPValidationError`, default title); a dump of the code is documentation, not a contract | warning |
| `route-before-contract` | with `history: true`: git says the operation id reached code before it reached the spec | `history_severity` (warning) |

`info` is a fourth severity: reported, counted separately, never fails a build and never promoted
by `strict`. The history check runs one `git log -p` over the spec and one over the project, so
it costs a few seconds; it compares commit times, so on a repo that squash-merges it can only see
contracts added in a later PR than the code, not later commits within one PR.

`init` proposes both blocks when it sees a `features/` directory with several children, or a
contract-named layer plus an `openapi.*` file, and guesses the mount prefix from the spec's paths.

### Baseline

Adopting a linter on a codebase with history should not require a big-bang cleanup.

```bash
archfence baseline          # writes archfence-baseline.json with today's violations
archfence scan              # now fails only on violations that are not in the baseline
archfence scan --no-baseline
```

Baseline entries ignore line numbers, so edits elsewhere in a file do not resurrect old
violations. Delete entries from the file as you fix them, or regenerate it.

### Starting from nothing

```bash
archfence init              # drafts archfence.yml: layers, rules, and what those rules flag today
```

`init` finds the source roots (each `pyproject.toml`, `Cargo.toml`, `.csproj`, `pubspec.yaml`),
proposes layers from the directory tree, prints the layer-by-layer import matrix, and drafts rules
from two sources:

- **What the names mean.** `domain` sits inside `services`, which sits inside `routers`;
  `infrastructure`, `data` and `presentation` are siblings in the outer ring; nothing imports
  `tests`. When an inner layer such as `domain` or `usecases` is named, the draft also adds the
  Clean Architecture expectation that UI layers do not import adapter layers directly.
- **What the code already does.** For layers whose names carry no convention, an arrow the code
  draws only one way (at least three imports, never the reverse) becomes a rule.

Every drafted rule is a **warning**, and `no_cycles` stays off if the layer graph has cycles
today, so the first scan reports without failing. `init` ends by running those rules and listing
what they flag right now. Delete the rules you disagree with, promote the ones that should fail a
build to `error`, and `archfence baseline` records the leftovers.

## Commands

```bash
archfence init [path]         # draft a config and show the dependency matrix
archfence warm                # load every grammar now
archfence scan [-v] [-f text|json|sarif] [-o file] [-p project] [--warn-only] [--baseline f] [--show-waived]
archfence baseline            # record current violations so only new ones fail
archfence routes              # HTTP routes found in source, with the contract prefix applied
archfence deps [--files]      # layer-to-layer dependency counts, optionally every edge
archfence unresolved          # imports that are not project code (i.e. your external deps)
```

`scan` exits 1 on any error-severity violation, so it drops straight into CI. The SARIF output uploads to
GitHub code scanning and annotates the PR.

## How resolution works

Each extractor emits logical names. C# files provide their declared namespaces and import
`using` directives. Python files provide dotted module paths and import absolute dotted names
(relative imports are resolved). TypeScript and JavaScript files provide their extension-less
path (and their directory, for `index.*`) and import ES imports, re-exports, `require()` and
dynamic `import()`, with `tsconfig.json` `paths` and `baseUrl` applied, including `extends`;
bare specifiers are packages. Dart files provide their path and `package:` URI and import
resolved URIs. Rust files provide `crate::module` paths and import `use` paths and `mod`
declarations, with `self`/`super` resolved, including inside inline `mod tests` blocks.

The core then does longest-prefix lookup: `using A.B.C.Type` resolves to the file declaring
namespace `A.B.C`, or `A.B` if that is all there is. When several crates or packages share
a name, a file's imports resolve within its own crate first. Anything that does not resolve
is an external dependency and is only subject to the `*_external` rules.

## Example

`examples/multi-language.yml` lints one repository holding an ASP.NET Core Clean Architecture
backend, a Flutter Clean Architecture + BLoC UI and a Rust agent, in one run. A few hundred files
across three languages scan in about a second.

## Layout and contributing

`CONTRIBUTING.md` describes the tree: a small core, one extractor per language, one vertical slice
per check, and `archfence.yml` at the root so archfence enforces its own layering in CI. Adding a
language is one extractor file plus a registry entry; adding a check is one module plus a registry
entry, and config loading, scanning and `init` pick it up.

## Tests

```bash
pip install -e '.[dev]' && archfence warm && pytest -q && archfence scan
```
