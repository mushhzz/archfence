import textwrap
from pathlib import Path

import pytest

from archfence.cli import main
from archfence.core.config import load_config
from conftest import write


def test_init_drafts_config_and_matrix(tmp_path, monkeypatch, capsys):
    write(tmp_path, "src/Shop.Domain/Shop.Domain.csproj", "<Project/>")
    write(tmp_path, "src/Shop.Domain/Order.cs", "namespace Shop.Domain;\n")
    write(tmp_path, "src/Shop.Api/Shop.Api.csproj", "<Project/>")
    write(tmp_path, "src/Shop.Api/C.cs", "using Shop.Domain;\nnamespace Shop.Api;\n")
    write(tmp_path, "web/pubspec.yaml", "name: w\n")
    write(tmp_path, "web/lib/domain/e.dart", "class E {}\n")
    write(tmp_path, "web/lib/presentation/p.dart", "import '../domain/e.dart';\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "[csharp] 2 files, 2 in proposed layers" in out
    assert "[dart] 2 files, 2 in proposed layers" in out
    assert "archfence: wrote archfence.yml" in out
    cfg = load_config(tmp_path / "archfence.yml")
    names = {p.name: [l.name for l in p.layers] for p in cfg.projects}
    assert names == {"csharp": ["api", "domain"], "dart": ["domain", "presentation"]}
    assert next(p for p in cfg.projects if p.name == "csharp").layer("domain").paths == ["src/Shop.Domain/**"]
    # drafted rules are warnings, so the draft never fails a build
    assert main(["scan"]) == 0
    assert main(["init"]) == 0
    assert "already exists; pass --force" in capsys.readouterr().out


def test_init_python_monorepo_roots(tmp_path, monkeypatch, capsys):
    # one packaged service with a single main package, plus loose packages at the repo root
    write(tmp_path, "apps/api/pyproject.toml", "[project]\nname='api'\n")
    for i in range(6):
        write(tmp_path, f"apps/api/app/routers/r{i}.py", "from app.services.s import f\n")
        write(tmp_path, f"apps/api/app/services/s{i}.py", "from app.models.m import M\n")
    write(tmp_path, "apps/api/app/__init__.py", "")
    write(tmp_path, "apps/api/app/main.py", "from app.routers import r0\n")
    write(tmp_path, "apps/api/app/models/m.py", "M = 1\n")
    write(tmp_path, "apps/api/tests/test_x.py", "from app.models.m import M\n")
    write(tmp_path, "collector/__init__.py", "")
    write(tmp_path, "collector/run.py", "from collector.plugins import a\n")
    write(tmp_path, "collector/plugins/a.py", "x = 1\n")
    write(tmp_path, "tests/test_collector.py", "from collector.run import main\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    cfg = load_config(tmp_path / "archfence.yml")
    by_name = {p.name: p for p in cfg.projects}
    assert set(by_name) == {"api", tmp_path.name.lower().replace("-", "_")}
    api = by_name["api"]
    assert api.root == "apps/api" and api.source_roots == ["."]
    assert [l.name for l in api.layers] == ["app", "models", "routers", "services", "tests"]
    assert api.layer("app").paths == ["app/*.py"]
    assert api.layer_for("app/main.py") == "app"
    assert api.layer_for("app/routers/r0.py") == "routers"
    assert "[api] 16 files, 16 in proposed layers" in out
    root = by_name[tmp_path.name.lower().replace("-", "_")]
    assert root.root == "." and "apps/api/**" in root.ignore
    assert [l.name for l in root.layers] == ["collector", "tests"]  # small package: not opened up
    assert main(["scan"]) == 0


def test_init_infers_rules(tmp_path, monkeypatch, capsys):
    write(tmp_path, "svc/pyproject.toml", "[project]\nname='svc'\n")
    write(tmp_path, "svc/app/__init__.py", "")
    for i in range(4):
        write(tmp_path, f"svc/app/routers/r{i}.py", "from app.services.s0 import f\nfrom app.plumbing.log import log\n")
        write(tmp_path, f"svc/app/services/s{i}.py", "from app.repositories.repo import R\nfrom app.plumbing.log import log\n")
    write(tmp_path, "svc/app/routers/bad.py", "from app.repositories.repo import R\n")  # router skipping the service layer
    write(tmp_path, "svc/app/repositories/repo.py", "R = 1\n")
    write(tmp_path, "svc/app/plumbing/log.py", "def log(): ...\n")
    write(tmp_path, "svc/app/main.py", "from app.routers import r0\n")
    write(tmp_path, "svc/tests/test_r.py", "from app.routers.r0 import f\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "every layer cannot_import tests  (nothing but tests imports tests)" in out
    assert "services cannot_import routers  ('services' is conventionally inside 'routers')" in out
    assert "repositories cannot_import services" in out
    # plumbing is unranked; the code only ever draws routers -> plumbing and services -> plumbing
    assert "plumbing cannot_import routers  (observed: routers -> plumbing x4, never the reverse)" in out
    assert "plumbing cannot_import services  (observed: services -> plumbing x4, never the reverse)" in out
    # no inner layer (domain/usecases) is named, so routers -> repositories is the classic layered shape and allowed
    assert "these rules flag 0 import(s) in the code today" in out
    assert "app cannot_import" not in out  # the package's own modules are cross-cutting, not a ring
    cfg = load_config(tmp_path / "archfence.yml")
    api = cfg.projects[0]
    assert api.layer("services").cannot_import == {"tests": "warning", "routers": "warning"}
    assert api.layer("plumbing").cannot_import == {"tests": "warning", "routers": "warning", "services": "warning"}
    assert api.check("layers").no_cycles == "warning"
    assert main(["scan"]) == 0
    assert "OK: no architecture errors" in capsys.readouterr().out


def test_init_clean_architecture_signal(tmp_path, monkeypatch, capsys):
    write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    write(tmp_path, "app/__init__.py", "")
    for i in range(5):
        write(tmp_path, f"app/routers/r{i}.py", "from app.services.s import f\n")
        write(tmp_path, f"app/services/s{i}.py", "from app.repositories.repo import R\nfrom app.domain.e import E\n")
    write(tmp_path, "app/routers/direct.py", "from app.repositories.repo import R\n")
    write(tmp_path, "app/repositories/repo.py", "from app.domain.e import E\n")
    write(tmp_path, "app/domain/e.py", "E = 1\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "routers cannot_import repositories  (Clean Architecture:" in out
    assert "these rules flag 1 import(s) in the code today:" in out
    assert "app/routers/direct.py:1: layer 'routers' must not import from layer 'repositories'" in out
    assert main(["scan"]) == 0  # still a warning
    assert "0 error(s), 1 warning(s)" in capsys.readouterr().out


def test_init_typescript_package(tmp_path, monkeypatch, capsys):
    write(tmp_path, "apps/web/package.json", '{"name": "web"}')
    write(tmp_path, "apps/web/node_modules/react/index.js", "module.exports = {}\n")
    write(tmp_path, "apps/web/next.config.js", "module.exports = {}\n")
    for i in range(3):
        write(tmp_path, f"apps/web/components/c{i}.tsx", "import { f } from '../lib/f';\n")
    write(tmp_path, "apps/web/lib/f.ts", "export const f = 1;\n")
    write(tmp_path, "apps/web/app/page.tsx", "import '../components/c0';\n")
    write(tmp_path, "apps/web/e2e/x.spec.ts", "import '../app/page';\n")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    cfg = load_config(tmp_path / "archfence.yml")
    p = cfg.projects[0]
    assert p.name == "typescript" and p.root == "apps/web" and p.languages == ["typescript"]
    assert [l.name for l in p.layers] == ["app", "components", "e2e", "lib"]
    assert "every layer cannot_import e2e" in out
    assert "lib cannot_import components  (observed: components -> lib x3, never the reverse)" in out
    assert "node_modules" not in out
    assert main(["scan"]) == 0
