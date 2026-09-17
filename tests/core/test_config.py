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
