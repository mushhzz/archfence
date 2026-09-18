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

    # a path-style target splits on "/", not on a dot inside a filename segment
    paths = {"x": ["p"], "x/y": ["q"]}
    assert resolve_target("x/y.z", paths) == ["p"]     # dir x, file "y.z" -> nearest package is x
    assert resolve_target("x/y/z", paths) == ["q"]     # under x/y
    # each language's own separator is used
    assert resolve_target("a::b::c", {"a::b": ["r"]}) == ["r"]


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


def test_to_utf8_decodes_bom_encodings():
    from archfence.core.graph import to_utf8
    assert to_utf8("using System;".encode("utf-16")) == b"using System;"       # UTF-16 LE (BOM)
    assert to_utf8("using System;".encode("utf-16-be")[:0] + b"\xfe\xff" + "using System;".encode("utf-16-be")) == b"using System;"
    assert to_utf8("using System;".encode("utf-32")) == b"using System;"       # UTF-32
    plain = b"using System;"
    assert to_utf8(plain) is plain                                             # UTF-8 passed through untouched
    assert to_utf8(b"\xef\xbb\xbfusing System;") == b"\xef\xbb\xbfusing System;"  # UTF-8 BOM left as-is


def test_utf16_source_files_are_parsed(tmp_path, monkeypatch, capsys):
    # A UTF-16-encoded source (older Windows C#/others) must be decoded and analysed, not treated as garbage.
    write(tmp_path, "app/infra/db.py", "x = 1\n")
    (tmp_path / "app" / "domain").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app/domain/m.py").write_bytes("from app.infra.db import x\n".encode("utf-16"))
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
    assert "app/domain/m.py:1: error[forbidden-import]" in out  # decoded and its import resolved
    assert "parse-error" not in out
