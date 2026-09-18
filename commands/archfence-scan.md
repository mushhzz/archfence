---
description: Run an archfence architecture scan and explain the results
---

Run an archfence architecture scan on this repository and report what it found.

1. Locate the `archfence.yml` (repo root, or ask if there are several). If none exists, run
   `archfence init`, summarize the drafted layers and rules, and stop for the user to review before scanning.
2. Run `archfence scan` from the directory containing the config. If `$ARGUMENTS` is non-empty, pass it
   through (e.g. `-p backend`, `-v`, `--show-waived`).
3. Interpret the **exit code**, not just the text: `0` = no error-severity violations (warnings may remain),
   `1` = error violations that gate CI, `2` = a config problem to fix (bad file, unknown key, no files matched).
4. Summarize the violations grouped by rule, point to the offending `file:line`, and for each suggest the
   real fix: correct the import, promote/demote a rule's severity, or add an `allow:` waiver with a reason if
   it is a genuine accepted exception. For coupling findings, offer `archfence metrics` for context.

Do not edit `archfence.yml` or add waivers without confirming with the user which violations are intentional.
