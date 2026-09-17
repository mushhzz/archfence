import textwrap
from pathlib import Path

import pytest

from archfence.cli import main
from archfence.core.graph import resolve_target
from conftest import write


def test_resolve_target_longest_prefix():
    idx = {"A.B": ["x.cs"], "A.B.C": ["y.cs"], "pkg": ["p/__init__.py"]}
    assert resolve_target("A.B.C", idx) == ["y.cs"]
    assert resolve_target("A.B.C.D", idx) == ["y.cs"]
    assert resolve_target("A.B.Z", idx) == ["x.cs"]
    assert resolve_target("A", idx) == []
    assert resolve_target("pkg.thing", idx) == ["p/__init__.py"]
    assert resolve_target("nothing::here", idx) == []


def test_same_name_crates_resolve_within_their_own_tree(tmp_path, monkeypatch, capsys):
    for name in ("a-agent", "b-agent"):
        write(tmp_path, f"{name}/Cargo.toml", '[package]\nname = "agent"\n')
        write(tmp_path, f"{name}/src/main.rs", "mod core;\nmod ui;\n")
        write(tmp_path, f"{name}/src/ui.rs", "pub fn f() {}\n")
    write(tmp_path, "a-agent/src/core.rs", "use crate::ui::f;\n#[cfg(test)]\nmod tests { use super::*; }\n")  # violation, in a-agent only
    write(tmp_path, "b-agent/src/core.rs", "pub fn g() {}\n")
    write(tmp_path, "archfence.yml", """
        languages: [rust]
        layers:
          core: { paths: ["*/src/core.rs"], cannot_import: [ui] }
          ui: { paths: ["*/src/ui.rs"] }
          entry: { paths: ["*/src/main.rs"] }
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "a-agent/src/core.rs:1: error[forbidden-import] layer 'core' must not import from layer 'ui' (agent::ui::f -> a-agent/src/ui.rs)" in out
    assert "b-agent/src/ui.rs" not in out
    assert "FAIL: 1 error(s)" in out
    # the test module's `use super::*` must not become an edge into b-agent's core.rs
    assert main(["deps", "--files"]) == 0
    assert "a-agent/src/core.rs:3" not in capsys.readouterr().out
