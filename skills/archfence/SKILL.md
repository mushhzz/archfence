---
name: archfence
description: Enforce and analyze software architecture with the archfence CLI - layer/import rules, vertical slices, API-contract-first checks, dependency cycles, and coupling metrics (fan-in/out, instability, dead code) across C#, Python, TypeScript/JavaScript, Dart and Rust. Use when the user wants to check or enforce architecture, set up or debug archfence or an archfence.yml, define which layers may import which, find dependency cycles or coupling hotspots, detect dead code, adopt a linter on an existing codebase without a big cleanup, or interpret archfence scan output and exit codes.
---

# archfence

archfence is a polyglot architecture linter. One `archfence.yml` describes layers and which layer may
import which; it parses code with tree-sitter and fails the build when an import crosses a forbidden line.
Install with `pip install archfence` (it exposes the `archfence` command).

## Quick start

```bash
archfence warm          # one-time: fetch tree-sitter grammars (also the step CI should cache)
archfence init          # draft archfence.yml from the tree; then read and edit it
archfence scan          # check the code against the config
```

Always run these from the directory holding `archfence.yml` (or pass `-c path/to/archfence.yml`).

## Exit codes (check these, do not just grep the text)

- `0` — no error-severity violations. Warnings and info may still be printed. `--warn-only` forces 0.
- `1` — at least one **error**-severity violation. This is the CI gate.
- `2` — configuration problem: bad/missing `archfence.yml`, an unknown config key (e.g. a typo like
  `cannot_imports:`), an unsupported language, or a project whose `root`/`languages` matched no files.
  Fix the config; do not treat a 2 as "clean".

## Workflows

**Adopt on an existing repo (do not hand-write the config blind):**
1. `archfence init` — drafts layers from the tree and rules from what layer names mean and what the code
   already does, then prints what those rules flag today. Every drafted rule is a `warning`.
2. Read the drafted `archfence.yml`. Delete rules you disagree with; promote the ones that should gate CI
   to `error`. `archfence deps` and `archfence deps --files` show the real edges behind a rule.
3. `archfence baseline` — writes `archfence-baseline.json` recording current violations, so `scan` then
   fails only on **new** ones. Fix and delete baseline entries over time.
4. Wire `archfence scan` into CI (exit 1 gates) and/or the pre-commit hook (id `archfence`).

**Investigate a violation:** run `archfence scan -v` (adds the layer matrix). Then `archfence deps --files`
for the exact edges, `archfence unresolved` for imports treated as external deps (a wrong `source_roots`
or crate/package layout often shows up here), `archfence routes` for the HTTP routes the contract checks
compare against the OpenAPI spec.

**Find coupling hotspots:** `archfence metrics` prints per-file fan-in/fan-out and per-layer instability
(`I = Ce/(Ca+Ce)`, 0 stable → 1 unstable). To gate on it, add a `metrics:` block (fan-in/out, `god_class`, dead code; see REFERENCE.md). `archfence watch` reruns the scan on every file change.

**Silence a known, accepted exception:** add an `allow:` waiver with a mandatory `reason`. Never loosen a
layer rule to hide one file; waive that path instead. `archfence scan --show-waived` lists what is hidden.

## Rules, severity, output

Every rule has a severity: `error` (fails the build), `warning` (reported, exit 0), `info` (advisory,
never fails, never promoted), `off`. `strict: true` on a project promotes every warning to error.
Output formats: `-f text` (default), `-f json`, `-f sarif` (uploads to GitHub code scanning). `-o FILE`
writes to a file. Rule ids include `forbidden-import`, `forbidden-external`, `layer-cycle`, `unlayered`,
`slice-coupling`, `route-not-in-contract`, `high-fan-out`, `high-fan-in`, `god-class`, `dead-code`, `parse-error`.

## Full configuration schema and command reference

See [REFERENCE.md](REFERENCE.md) for every config key (layers, rules, waivers, slices, contracts, metrics,
presets) with examples, and the full per-command flag list.
