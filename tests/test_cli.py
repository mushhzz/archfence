import textwrap
from pathlib import Path

import pytest

from archfence.cli import main
from conftest import write


def test_cli_exit_codes_and_formats(csharp_project, capsys, monkeypatch):
    monkeypatch.chdir(csharp_project)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "FAIL: 2 error(s) (1 warning(s))" in out
    assert main(["scan", "--warn-only"]) == 0
    assert main(["scan", "-f", "sarif", "-o", "out.sarif"]) == 1
    sarif = (csharp_project / "out.sarif").read_text()
    assert '"version": "2.1.0"' in sarif and '"ruleId": "forbidden-import"' in sarif
    assert main(["scan", "-f", "json"]) == 1
    assert '"violations"' in capsys.readouterr().out
    assert main(["deps"]) == 0
    assert "application      -> domain" in capsys.readouterr().out


def test_empty_project_is_a_config_error(tmp_path, monkeypatch, capsys):
    write(tmp_path, "src/a.py", "x = 1\n")
    write(tmp_path, "archfence.yml", "languages: [python]\nroot: nowhere\nlayers:\n  a: {paths: ['**']}\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 2
    err = capsys.readouterr().err
    assert "no python files under" in err and "check `root`, `languages` and `ignore`" in err


def test_warm(capsys):
    assert main(["warm"]) == 0
    assert "grammars ready: csharp, python, dart, rust, typescript, tsx, javascript" in capsys.readouterr().out
