import textwrap
from pathlib import Path

import pytest

from archfence.cli import main
from archfence.core.config import load_config
from conftest import write


def test_severity_forms_and_strict(leaky_project, monkeypatch, capsys):
    monkeypatch.chdir(leaky_project)
    write(leaky_project, "archfence.yml", """
        languages: [python]
        layers:
          domain:
            paths: ["app/domain/**"]
            cannot_import_external: {sqlalchemy: warning}
          services:
            paths: ["app/services/**"]
            severity: warning
            cannot_import: {adapters: error}
            cannot_import_external: [requests]
          adapters: { paths: ["app/adapters/**"] }
    """)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/domain/m.py:1: warning[forbidden-external]" in out
    assert "app/services/s.py:1: error[forbidden-import]" in out  # per-rule override beats layer default
    assert "app/services/s.py:2: warning[forbidden-external]" in out  # layer default
    assert "FAIL: 1 error(s) (2 warning(s))" in out

    write(leaky_project, "archfence.yml", """
        strict: true
        languages: [python]
        layers:
          domain: { paths: ["app/domain/**"], cannot_import_external: {sqlalchemy: warning} }
          services: { paths: ["app/services/**"], severity: off, cannot_import: [adapters] }
          adapters: { paths: ["app/adapters/**"] }
    """)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/domain/m.py:1: error[forbidden-external]" in out  # strict promotes warnings
    assert "app/services" not in out  # severity: off drops the rule entirely
    assert "FAIL: 1 error(s)" in out and "warning(s)" not in out.splitlines()[-1]


def test_waivers(leaky_project, monkeypatch, capsys):
    monkeypatch.chdir(leaky_project)
    write(leaky_project, "archfence.yml", """
        languages: [python]
        layers:
          domain: { paths: ["app/domain/**"], cannot_import_external: [sqlalchemy] }
          services: { paths: ["app/services/**"], cannot_import: [adapters] }
          adapters: { paths: ["app/adapters/**"] }
        allow:
          - path: "app/services/s.py"
            layers: [adapters]
            reason: "legacy: to be removed in ticket ARCH-12"
          - path: "app/domain/**"
            external: [sqlalchemy]
            reason: "declarative models live in domain for now"
    """)
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "OK: no architecture errors (2 waived)" in out
    assert "waived[" not in out
    assert main(["scan", "--show-waived"]) == 0
    assert "app/services/s.py:1: waived[forbidden-import]" in capsys.readouterr().out


def test_waiver_requires_reason(tmp_path):
    write(tmp_path, "archfence.yml", "languages: [python]\nlayers:\n  a: {paths: ['a/**']}\nallow:\n  - path: 'a/**'\n")
    with pytest.raises(Exception, match="needs a 'reason'"):
        load_config(tmp_path / "archfence.yml")


def test_baseline_only_fails_on_new(leaky_project, monkeypatch, capsys):
    monkeypatch.chdir(leaky_project)
    write(leaky_project, "archfence.yml", """
        languages: [python]
        layers:
          domain: { paths: ["app/domain/**"], can_only_import: [], cannot_import_external: [sqlalchemy] }
          services: { paths: ["app/services/**"], cannot_import: [adapters] }
          adapters: { paths: ["app/adapters/**"] }
    """)
    assert main(["scan"]) == 1
    capsys.readouterr()
    assert main(["baseline"]) == 0
    assert "recorded 2 violation(s)" in capsys.readouterr().out
    assert (leaky_project / "archfence-baseline.json").exists()

    assert main(["scan"]) == 0
    assert "OK: no architecture errors (2 baselined)" in capsys.readouterr().out

    # a new violation, plus moving an old one to a different line, which must not count as new
    write(leaky_project, "app/domain/m.py", "# comment\nimport sqlalchemy\n")
    write(leaky_project, "app/domain/n.py", "from app.adapters.db import x\n")
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/domain/n.py:1: error[forbidden-import]" in out
    assert "app/domain/m.py" not in out
    assert "FAIL: 1 error(s) (2 baselined)" in out
    assert main(["scan", "--no-baseline"]) == 1
    assert "FAIL: 3 error(s)" in capsys.readouterr().out
