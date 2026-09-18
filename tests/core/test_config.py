import textwrap
from pathlib import Path

import pytest

from archfence.core.config import ConfigError, load_config
from archfence.core.globs import glob_match
from conftest import write


def test_glob_variants():
    assert glob_match("src/App.Domain/Entities/Host.cs", "src/*.Domain/**")
    assert glob_match("src/App.Domain/Host.cs", "src/*.Domain/**")
    assert not glob_match("src/App.Application/Host.cs", "src/*.Domain/**")
    assert glob_match("lib/core/di/injection.dart", "lib/core/di/**")
    assert glob_match("a/src/config.rs", "*/src/{config,identity}.rs")
    assert glob_match("a/src/identity.rs", "*/src/{config,identity}.rs")
    assert not glob_match("a/src/queue.rs", "*/src/{config,identity}.rs")
    assert glob_match("x/src/remote_ipc.rs", "*/src/{remote_*,pty}.rs")
    assert glob_match("lib/main.dart", "lib/main.dart")
    # `*` must not cross a directory separator
    assert glob_match("app/settings.py", "app/*.py")
    assert not glob_match("app/patching/x.py", "app/*.py")
    assert not glob_match("lib/x/y.dart", "lib/*.dart")
    assert glob_match("a/b/domain/c/d.py", "**/domain/**")
    assert glob_match("domain/d.py", "**/domain/**")
    assert not glob_match("a/domainx/d.py", "**/domain/**")
    assert glob_match("x/y/z.py", "x/")
    assert glob_match("apps/api/tests/t.py", "apps/api/**")
    assert not glob_match("apps/api2/t.py", "apps/api/**")


def test_load_multi_project_with_preset(tmp_path):
    cfg = tmp_path / "archfence.yml"
    cfg.write_text(
        textwrap.dedent(
            """
            projects:
              backend:
                extends: clean-architecture-dotnet
                layers:
                  domain: { paths: ["src/Foo.Domain/**"] }
              web:
                languages: [dart]
                root: web
                layers:
                  a: { paths: ["lib/a/**"], cannot_import: [b] }
                  b: { paths: ["lib/b/**"] }
            """
        )
    )
    config = load_config(cfg)
    assert [p.name for p in config.projects] == ["backend", "web"]
    backend = config.projects[0]
    # preset layers survive, overridden path applies, preset rules kept
    assert backend.layer("domain").paths == ["src/Foo.Domain/**"]
    assert backend.layer("application").can_only_import == ["domain"]
    assert "Microsoft.EntityFrameworkCore" in backend.layer("domain").cannot_import_external


def test_unknown_layer_reference_is_an_error(tmp_path):
    cfg = tmp_path / "archfence.yml"
    cfg.write_text("languages: [python]\nlayers:\n  a: {paths: ['a/**'], cannot_import: [zzz]}\n")
    with pytest.raises(ConfigError, match="unknown layer 'zzz'"):
        load_config(cfg)


def test_unknown_preset(tmp_path):
    cfg = tmp_path / "archfence.yml"
    cfg.write_text("extends: nope\n")
    with pytest.raises(ConfigError, match="unknown preset"):
        load_config(cfg)


def test_most_specific_layer_wins(tmp_path):
    cfg = tmp_path / "archfence.yml"
    cfg.write_text(
        "languages: [dart]\nlayers:\n  core: {paths: ['lib/core/**']}\n  router: {paths: ['lib/core/router/**']}\n  file: {paths: ['lib/core/router/special.dart']}\n"
    )
    project = load_config(cfg).projects[0]
    assert project.layer_for("lib/core/theme/x.dart") == "core"
    assert project.layer_for("lib/core/router/app_router.dart") == "router"
    assert project.layer_for("lib/core/router/special.dart") == "file"
    assert project.layer_for("lib/other.dart") is None


def _err(tmp_path, body):
    (tmp_path / "archfence.yml").write_text(textwrap.dedent(body))
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(tmp_path / "archfence.yml")


def test_typo_in_rule_key_is_rejected(tmp_path):
    # The headline footgun: a mistyped rule key must fail loudly, not silently disable the rule.
    _err(tmp_path, """
        languages: [python]
        layers:
          domain: { paths: ["a/**"], cannot_imports: [infra] }
          infra: { paths: ["b/**"] }
    """)


def test_unknown_project_key_is_rejected(tmp_path):
    _err(tmp_path, """
        languages: [python]
        stricy: true
        layers:
          a: { paths: ["a/**"] }
    """)


def test_unknown_waiver_key_is_rejected(tmp_path):
    _err(tmp_path, """
        languages: [python]
        layers:
          a: { paths: ["a/**"] }
        allow:
          - path: "a/**"
            reason: "x"
            layerz: [a]
    """)


def test_unknown_slices_and_contracts_keys_are_rejected(tmp_path):
    _err(tmp_path, """
        languages: [python]
        layers:
          schemas: { paths: ["s/**"] }
          routers: { paths: ["r/**"] }
        slices:
          roots: ["feat/*"]
          shard: ["core/**"]
    """)
    _err(tmp_path, """
        languages: [python]
        layers:
          schemas: { paths: ["s/**"] }
          routers: { paths: ["r/**"] }
        contracts:
          layer: schemas
          consumers: [routers]
          openapi: { path: o.yaml, prefx: /api }
    """)


def test_stray_top_level_key_beside_projects_is_rejected(tmp_path):
    _err(tmp_path, """
        projcts:
          web:
            languages: [python]
            layers:
              a: { paths: ["a/**"] }
    """)


def test_known_keys_still_load(tmp_path):
    # Every documented project/layer/check key together must load without error.
    (tmp_path / "archfence.yml").write_text(textwrap.dedent("""
        languages: [python]
        root: .
        strict: false
        source_roots: ["."]
        ignore: ["**/migrations/**"]
        allow_unlayered: true
        no_cycles: warning
        layers:
          schemas: { paths: ["s/**"], severity: warning, can_only_import: [], cannot_import: {routers: error},
                     cannot_import_external: {os: warning}, can_only_import_external: ["typing"] }
          routers: { paths: ["r/**"] }
        allow:
          - { path: "r/**", layers: [schemas], external: [os], rules: [forbidden-import], reason: "ok" }
        slices: { roots: ["feat/*"], shared: ["core/**"], shared_only: true, shared_imports_slices: off, severity: warning, allow: {a: [b]} }
        contracts:
          layer: schemas
          consumers: [routers]
          allow: [routers]
          severity: warning
          openapi: { path: o.yaml, prefix: /api, match: both, severity: warning, pending: info, history: false, history_severity: warning, ignore: ["/health*"] }
    """))
    cfg = load_config(tmp_path / "archfence.yml")
    assert cfg.projects[0].check("layers").no_cycles == "warning"
