# Changelog

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
