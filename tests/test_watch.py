import time

import pytest

from archfence.cli import _fs_signature, _select, main, scan_pass
from archfence.core.config import load_config
from conftest import write


def _project(tmp_path):
    write(tmp_path, "app/domain/m.py", "from app.infra.db import x\n")
    write(tmp_path, "app/infra/db.py", "x = 1\n")
    write(tmp_path, "archfence.yml",
          "languages: [python]\nsource_roots: [.]\nlayers:\n"
          "  domain: { paths: [\"app/domain/**\"], cannot_import: [infra] }\n"
          "  infra: { paths: [\"app/infra/**\"] }\n")
    return load_config(tmp_path / "archfence.yml")


def test_scan_pass_reports_violations(tmp_path):
    config = _project(tmp_path)
    report = scan_pass(config, config.projects)
    assert "forbidden-import" in report and "FAIL: 1 error(s)" in report


def test_fs_signature_changes_when_a_file_changes(tmp_path):
    config = _project(tmp_path)
    projects = _select(config, None)
    before = _fs_signature(config, projects)
    time.sleep(0.01)
    write(tmp_path, "app/infra/new.py", "y = 2\n")
    after = _fs_signature(config, projects)
    assert after != before and any("new.py" in k for k in after)


def test_watch_runs_a_pass_then_stops_on_interrupt(tmp_path, monkeypatch, capsys):
    _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = {"n": 0}

    def fake_sleep(_):
        calls["n"] += 1
        raise KeyboardInterrupt  # stop after the first scan pass

    monkeypatch.setattr(time, "sleep", fake_sleep)
    assert main(["watch", "--interval", "0.1"]) == 0
    out = capsys.readouterr().out
    assert "watching for changes" in out
    assert "forbidden-import" in out  # it ran a real scan before the interrupt
    assert "stopped" in out
