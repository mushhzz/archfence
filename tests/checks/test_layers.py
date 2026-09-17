import textwrap
from pathlib import Path

import pytest

from archfence.checks import run_checks
from archfence.cli import main
from archfence.core.config import load_config
from archfence.core.graph import build_graph
from conftest import write


def test_csharp_violations(csharp_project):
    config = load_config(csharp_project / "archfence.yml")
    graph = build_graph(csharp_project, config.projects[0])
    violations, waived = run_checks(graph)
    assert waived == []
    by_rule = {}
    for v in violations:
        by_rule.setdefault(v.rule, []).append(v)

    ext = by_rule["forbidden-external"]
    assert len(ext) == 1 and ext[0].path == "src/App.Domain/Bad.cs" and ext[0].line == 1
    assert ext[0].severity == "warning"  # framework bans are warnings in the preset

    imp = by_rule["forbidden-import"]
    assert [(v.path, v.src_layer, v.dst_layer) for v in imp] == [("src/App.Application/Leak.cs", "application", "infrastructure")]

    cyc = by_rule["layer-cycle"]
    assert len(cyc) == 1
    assert cyc[0].message.endswith("application -> infrastructure -> application")

    # MediatR in the application layer is allowed by the preset; the WebApi may see everything.
    assert not any(v.path.startswith("src/App.WebApi") for v in violations)


def test_clean_project_passes(tmp_path, capsys, monkeypatch):
    write(tmp_path, "svc/domain/model.py", "x = 1\n")
    write(tmp_path, "svc/services/use.py", "from svc.domain.model import x\nfrom ..domain import model\n")
    write(tmp_path, "svc/api/routes.py", "from svc.services.use import x\nimport fastapi\n")
    write(tmp_path, "archfence.yml", """
        extends: python-layered
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "-v"]) == 0
    out = capsys.readouterr().out
    assert "OK: no architecture errors" in out
    assert "services -> domain: 2" in out  # two import lines, deduplicated per line


def test_python_domain_importing_adapter_fails(tmp_path, monkeypatch, capsys):
    write(tmp_path, "svc/domain/model.py", "from svc.adapters.db import Session\n")
    write(tmp_path, "svc/adapters/db.py", "Session = object\n")
    write(tmp_path, "archfence.yml", "extends: python-layered\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    assert "layer 'domain' may only import from []" in capsys.readouterr().out


def test_dart_and_rust_end_to_end(tmp_path, monkeypatch, capsys):
    write(tmp_path, "web/pubspec.yaml", "name: ui\n")
    write(tmp_path, "web/lib/domain/e.dart", "class E {}\n")
    write(tmp_path, "web/lib/data/r.dart", "import '../domain/e.dart';\nimport 'package:flutter/material.dart';\n")
    write(tmp_path, "web/lib/presentation/s.dart", "import 'package:ui/data/r.dart';\nimport 'dart:io';\n")
    write(tmp_path, "agent/Cargo.toml", '[package]\nname = "agent"\n')
    write(tmp_path, "agent/src/main.rs", "mod core;\nmod ui;\n")
    write(tmp_path, "agent/src/core.rs", "use crate::ui::show;\n")
    write(tmp_path, "agent/src/ui.rs", "pub fn show() {}\n")
    write(tmp_path, "archfence.yml", """
        projects:
          web:
            extends: flutter-clean-bloc
            root: web
          agent:
            languages: [rust]
            root: agent
            layers:
              core: { paths: ["src/core.rs"], cannot_import: [ui] }
              ui: { paths: ["src/ui.rs"] }
              entry: { paths: ["src/main.rs"] }
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "web/lib/data/r.dart" not in out  # path is relative to the project root
    assert "lib/data/r.dart:2: warning[forbidden-external] layer 'data' must not depend on 'package:flutter/material.dart'" in out
    assert "lib/presentation/s.dart:2: warning[forbidden-external] layer 'presentation' must not depend on 'dart:io'" in out
    assert "lib/presentation/s.dart:1: error[forbidden-import] layer 'presentation' may only import from ['domain', 'core', 'di', 'router']; found 'data' (lib/data/r.dart)" in out
    assert "src/core.rs:1: error[forbidden-import] layer 'core' must not import from layer 'ui' (agent::ui::show -> src/ui.rs)" in out
    assert "FAIL: 2 error(s) (2 warning(s))" in out


def test_typescript_end_to_end(tmp_path, monkeypatch, capsys):
    write(tmp_path, "web/package.json", '{"name": "web"}')
    write(tmp_path, "web/tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./*"]}}}')
    write(tmp_path, "web/lib/api/client.ts", "export const get = () => 1;\n")
    write(tmp_path, "web/components/ui/button.tsx", "export const Button = 1;\n")
    write(tmp_path, "web/components/board/deck.tsx", "import { Button } from '@/components/ui/button';\nimport { get } from '@/lib/api/client';\n")
    write(tmp_path, "web/lib/api/bad.ts", "import { Button } from '@/components/ui/button';\n")
    write(tmp_path, "web/app/page.tsx", "import { Deck } from '../components/board/deck';\n")
    write(tmp_path, "archfence.yml", """
        languages: [typescript]
        root: web
        layers:
          app: { paths: ["app/**"] }
          components: { paths: ["components/**"] }
          lib: { paths: ["lib/**"], cannot_import: [components, app] }
    """)
    monkeypatch.chdir(tmp_path)
    assert main(["scan", "-v"]) == 1
    out = capsys.readouterr().out
    assert "lib/api/bad.ts:1: error[forbidden-import] layer 'lib' must not import from layer 'components' (components/ui/button -> components/ui/button.tsx)" in out
    assert "components -> lib: 1" in out and "app -> components: 1" in out
    assert "layer-cycle] dependency cycle between layers: components -> lib -> components" in out
    assert "FAIL: 2 error(s)" in out
