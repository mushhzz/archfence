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


def test_layer_cycle_baseline_key_is_stable_across_representative_edge():
    from archfence.core.baseline import key
    from archfence.core.model import Violation

    def cyc(path, line, target, dst, msg):
        return Violation("layer-cycle", msg, path, line, "a", dst, target, "proj", "error")

    m = "dependency cycle between layers: a -> b -> a"
    # same cycle, detected through two different edges/files/targets -> one baseline identity
    v1 = cyc("a/one.py", 3, "b.thing", "b", m)
    v2 = cyc("a/two.py", 9, "b.other", "b", m)
    assert key(v1) == key(v2)
    # a genuinely different cycle is a different identity
    assert key(v1) != key(cyc("a/x.py", 1, "c.z", "c", "dependency cycle between layers: a -> c -> a"))


def test_parse_error_is_not_flagged_when_imports_were_still_extracted(tmp_path, monkeypatch, capsys):
    # A syntax error below the imports (tree-sitter recovers) must NOT raise parse-error: the imports were
    # captured, so it is a false alarm. The captured import must still be analysed.
    write(tmp_path, "app/domain/leaky.py", "from app.infra.db import thing\n\ndef broken(((  !!! not valid\n")
    write(tmp_path, "app/infra/db.py", "thing = 1\n")
    write(tmp_path, "archfence.yml", """
        languages: [python]
        source_roots: [.]
        layers:
          domain: { paths: ["app/domain/**"], cannot_import: [infra] }
          infra: { paths: ["app/infra/**"] }
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "parse-error" not in out                          # no false positive
    assert "app/domain/leaky.py:1: error[forbidden-import]" in out  # the import was still analysed


def test_parse_error_is_surfaced_only_when_nothing_was_extracted(tmp_path, monkeypatch, capsys):
    # C# provides come from the parse, so a file the grammar cannot parse at all yields no imports, no
    # namespace and no types: nothing to analyse. That is the genuine case worth a warning.
    write(tmp_path, "src/App/ok.cs", "namespace App; class Ok {}\n")
    write(tmp_path, "src/App/garbage.cs", "@#$%^&*( this is not c# at all )*&^%$#@\n")
    write(tmp_path, "archfence.yml", """
        languages: [csharp]
        layers:
          app: { paths: ["src/App/**"] }
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 0  # a parse error is a warning, not a build failure
    out = capsys.readouterr().out
    assert "src/App/garbage.cs:1: warning[parse-error]" in out
    assert "1 warning(s)" in out

    # it is waivable like any other finding
    write(tmp_path, "archfence.yml", """
        languages: [csharp]
        layers:
          app: { paths: ["src/App/**"] }
        allow:
          - { path: "src/App/garbage.cs", rules: [parse-error], reason: "vendored, ignore" }
    """)
    assert main(["scan"]) == 0
    assert "parse-error" not in capsys.readouterr().out
