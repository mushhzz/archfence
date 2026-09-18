# Changelog

## 0.2.0

### Added

- **Coupling metrics** (`metrics` check + `archfence metrics` command): afferent/efferent coupling over the
  resolved import graph, the same for every language. `fan_out`, `fan_in` and `god_class` are gate-able rules
  (`high-fan-out`, `high-fan-in`, `god-class`) that obey severity, `strict`, waivers and the baseline;
  `dead_code` flags files nothing imports (route-declaring files and declared entrypoints excepted). The
  command also reports per-layer instability `I = Ce / (Ca + Ce)` and the largest types.
- **`archfence watch`**: rescans whenever a file under the project root changes; a config or parse error is
  shown and the loop keeps running.
- **Claude Code plugin**: an `archfence` skill (`skills/archfence/`) that teaches an agent to drive the CLI,
  an `/archfence-scan` command, and a `.claude-plugin/plugin.json` manifest. It drives the installed CLI.

### Fixed

- Config loading rejects unknown keys at every level (project, layer, waiver, `slices`, `contracts`,
  `contracts.openapi`). A typo such as `cannot_imports:` was silently accepted and disabled the rule; it is
  now an error (exit 2) that names the bad key and lists the valid ones.
- Source files encoded as UTF-16/UTF-32 (common for older Windows/Visual Studio C#) are decoded to UTF-8 via
  their byte-order mark before parsing, instead of being handed to the parser as bytes and dropped entirely.
- `parse-error` (new warning) fires only when a file cannot be read, or cannot be parsed at all (no imports,
  namespaces or types extracted). A file the grammar recovers from with its imports intact is not flagged, so
  a deep syntax error below the imports (e.g. a C# `#if` block) is not a false alarm. Waivable like any rule.
- Import resolution selects a target's separator as `::` then `/` then `.`, so a path-style target with a dot
  in a filename segment is no longer mis-split.
- The git-history check's C# operation-id detection requires `Name = "..."` to sit on a routing attribute
  (`[HttpGet ...]`, `[Route ...]`), so an ordinary `Name = "..."` no longer trips `route-before-contract`.
- `layer-cycle` baseline entries are keyed by the set of layers in the cycle, so moving the offending import
  between files no longer resurrects a baselined cycle.

## 0.1.1

- Restructured into a small core, one extractor per language and one vertical slice per check,
  with `archfence.yml` at the root so CI runs archfence on archfence. No behaviour or config change.
- Release workflow tolerates re-runs of an already published version.


## 0.1.0

First public release.

- Layer dependency rules (`cannot_import`, `can_only_import`, external-package bans), layer
  cycles, per-rule severities (`error` / `warning` / `info` / `off`), `strict`, waivers with a
  mandatory reason, and a baseline file for adopting on existing code.
- Vertical slices: slices must not import siblings; shared code must not import slices.
- API contract first: contract layer purity, route-declaring files must import contract types,
  OpenAPI drift with contract-first asymmetry, generated-spec detection, and an opt-in git history
  check that the operation id reached the spec before the code.
- Languages: C#, Python, TypeScript/JavaScript, Dart, Rust via tree-sitter.
- `archfence init` drafts layers from the tree and rules from what layer names mean and what the
  code already does, then shows what those rules flag today.
- Text, JSON and SARIF output; `deps`, `unresolved`, `routes`, `warm` commands; pre-commit hook and
  GitHub Actions example.
