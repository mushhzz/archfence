# archfence reference

## Config file

`archfence.yml` at the repo root. Either one project inline, or several under `projects:`. `root` is
relative to the config file. Unknown keys are a hard error (exit 2), so a typo never silently no-ops.

```yaml
projects:
  backend:
    extends: clean-architecture-dotnet   # optional preset; you override its paths
    languages: [csharp]                  # csharp | python | typescript | dart | rust (aliases: cs, py, ts, js, tsx, flutter, rust, rs, dotnet)
    root: .
    ignore: ["**/Migrations/**"]
    source_roots: ["src"]                # Python only: dotted-module roots
    strict: false                        # true = promote every warning to error
    layers:
      domain:         { paths: ["src/App.Domain/**"] }
      application:    { paths: ["src/App.Application/**"] }
      infrastructure: { paths: ["src/App.Infrastructure/**"] }
      webapi:         { paths: ["src/App.WebApi/**"] }
```

### Per-layer keys

| key | meaning |
|---|---|
| `paths` | globs relative to `root`; `**`, `*`, `{a,b}` supported. Most specific matching layer wins. |
| `cannot_import` | layers this layer must not import (list, or `{layer: severity}`) |
| `can_only_import` | complete allow-list of layers (empty list = nothing but itself) |
| `cannot_import_external` | forbidden third-party name prefixes (`Microsoft.EntityFrameworkCore`, `package:flutter/`, `sqlalchemy`, `tokio::`) |
| `can_only_import_external` | allow-list version of the above |
| `severity` | default severity for every rule on this layer |

Project-level: `no_cycles` (`error`/`warning`/`off`, default `error`), `allow_unlayered` (default true).

### Severity

`error` fails the build; `warning` is reported at exit 0; `info` is advisory (never fails, never promoted
by `strict`); `off` drops the rule. Per-rule: `cannot_import: {adapters: error, jobs: warning}`.

### Waivers — documented, accepted exceptions

```yaml
allow:
  - path: "lib/presentation/legacy/**"
    layers: [data]                # narrow to a dst layer (optional)
    external: [System.Text.Json]  # narrow to an external prefix (optional)
    rules: [layer-cycle]          # narrow to rule ids (optional)
    reason: "pre-BLoC screens, tracked in ARCH-12"   # REQUIRED
```

Omit `layers`/`external`/`rules` to waive everything under `path`. `reason` is mandatory.

### Vertical slices

```yaml
slices:
  roots: ["app/features/*"]     # each child dir is a slice; siblings must not import each other
  shared: ["app/core/**"]       # code every slice may use
  shared_only: false            # true: a slice may import only itself and shared
  allow: { reports: [users] }   # explicit slice -> slices it may import
  severity: error
  shared_imports_slices: error
```

Rules: `slice-coupling`, `shared-imports-slice`, `slice-escape`.

### API contract first

```yaml
contracts:
  layer: schemas                # the contract layer; imports nothing (or only `allow`)
  consumers: [routers]          # every route-declaring file here must import contract types
  allow: [domain]
  severity: error
  openapi:
    path: docs/openapi.yaml
    prefix: /api/v1             # mounted in front of every code route
    match: both                # operation_id | path | both
    ignore: ["/health*"]
    severity: warning
    pending: info
    history: false             # git: the operation id must reach the spec no later than the code
    history_severity: warning
```

Rules: `route-not-in-contract`, `route-contract-mismatch`, `contract-pending`, `openapi-generated`,
`route-before-contract`, `contract-depends-on-code`, `route-without-contract`. Routes are read from
FastAPI/Starlette/Flask decorators and ASP.NET `[Route]`/`[HttpGet]` attributes; `archfence routes` lists them.

### Coupling metrics

```yaml
metrics:
  fan_out: { max: 15, severity: warning }   # or shorthand `fan_out: 15`
  fan_in:  { max: 30, severity: error }
  god_class: { max_methods: 20, severity: warning }   # or shorthand `god_class: 20`
  dead_code:                                 # opt-in; route-declaring files count as entrypoints
    severity: warning
    entrypoints: ["src/**/main.py", "**/__main__.py"]
    exclude: ["**/generated/**"]
```

Rules: `high-fan-out`, `high-fan-in`, `god-class`, `dead-code`. All thresholds obey `strict`, waivers and the baseline.

### Presets (`extends`)

`clean-architecture-dotnet`, `flutter-clean-bloc`, `python-layered`, `rust-hexagonal`. A preset supplies
layers and rules; your project overrides the paths.

## Commands

| command | purpose | key flags |
|---|---|---|
| `scan` | check the code; exit 1 on error violations | `-f text/json/sarif`, `-o FILE`, `-p PROJECT`, `-v`, `--warn-only`, `--baseline F`, `--no-baseline`, `--show-waived` |
| `init [path]` | draft `archfence.yml` and show the matrix | `-o FILE` (`-` to skip), `--force` |
| `baseline` | record current violations so only new ones fail | `-o FILE` (default `archfence-baseline.json`) |
| `deps` | layer-to-layer dependency counts | `--files` (every edge with locations), `-p PROJECT` |
| `metrics` | per-file fan-in/fan-out, per-layer instability, largest types | `--top N`, `-p PROJECT` |
| `watch` | rescan on every file change until Ctrl-C | `--interval N`, `-p PROJECT` |
| `unresolved` | imports treated as external (your deps) | `--top N`, `-p PROJECT` |
| `routes` | HTTP routes found in source | `-p PROJECT` |
| `warm` | load every grammar now (fresh machines / CI cache) | |

All commands except `init`/`warm` take `-c/--config`.

## CI and pre-commit

- CI: cache the grammar dir, run `archfence warm` then `archfence scan`. `scan -f sarif -o out.sarif`
  uploads to GitHub code scanning. See `examples/github-action.yml` in the archfence repo.
- pre-commit: the repo ships `.pre-commit-hooks.yaml` (hook id `archfence`); it runs `archfence scan` over
  the whole repo (`always_run`, `pass_filenames: false`), because a violation can span unstaged files.

## Reading resolution (why an import did or did not count)

Each extractor emits logical names: C# namespaces, Python dotted modules, TS/JS extensionless paths (with
`tsconfig` `paths`/`baseUrl`), Dart `package:` URIs, Rust `crate::` paths. Resolution is longest-prefix and
downward-only: `using A.B.C` resolves to the file declaring `A.B` if none declares `A.B.C`, but importing a
parent package never fans out to its children. An import that resolves to no project file is an external
dependency (only the `*_external` rules apply) and shows up under `archfence unresolved`.
