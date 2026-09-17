import textwrap
from pathlib import Path

import pytest

from archfence.cli import main
from archfence.core.config import ConfigError, load_config
from conftest import write


def test_slices_rules(sliced, monkeypatch, capsys):
    monkeypatch.chdir(sliced)
    write(sliced, "archfence.yml", """
        languages: [python]
        layers:
          app: { paths: ["app/**"] }
        slices:
          roots: ["app/features/*"]
          shared: ["app/shared/**"]
          allow:
            reports: [users]
    """)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/features/billing/api.py:2: error[slice-coupling] slice 'billing' must not import slice 'users'" in out
    assert "app/shared/leak.py:1: error[shared-imports-slice] shared code must not depend on slice 'billing'" in out
    assert "reports" not in out  # explicitly allowed to use users
    assert "FAIL: 2 error(s)" in out


def test_slices_shared_only(sliced, monkeypatch, capsys):
    monkeypatch.chdir(sliced)
    write(sliced, "archfence.yml", """
        languages: [python]
        layers:
          app: { paths: ["app/**"] }
        slices:
          roots: ["app/features/*"]
          shared: ["app/shared/**"]
          shared_only: true
          severity: warning
          shared_imports_slices: off
    """)
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "app/features/reports/build.py:2: warning[slice-escape] slice 'reports' may only import shared code, not app.legacy.util" in out
    assert "shared-imports-slice" not in out
    assert "0 error(s), 3 warning(s)" in out  # billing->users, reports->users, reports->legacy


def test_shared_dir_under_slice_root_is_not_a_slice(tmp_path, monkeypatch, capsys):
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/core/db.py", "x = 1\n")
    write(tmp_path, "app/billing/s.py", "from app.core.db import x\nfrom app.users.r import r\n")
    write(tmp_path, "app/users/r.py", "r = 1\n")
    write(tmp_path, "archfence.yml", """
        languages: [python]
        layers:
          app: { paths: ["app/**"] }
        slices:
          roots: ["app/*"]
          shared: ["app/core/**"]
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "slice 'billing' must not import slice 'users'" in out
    assert "slice 'core'" not in out and "must not import slice 'core'" not in out
    assert "FAIL: 1 error(s)" in out
