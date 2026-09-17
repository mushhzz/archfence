import textwrap
from pathlib import Path

import pytest

from archfence.extractors.csharp import CSharpExtractor
from archfence.extractors.dart import DartExtractor
from archfence.extractors.python import PythonExtractor
from archfence.extractors.rust import RustExtractor
from conftest import write


def targets(sf):
    return [i.target for i in sf.imports]


def test_csharp_namespaces_and_usings(tmp_path):
    src = b"""
using System;
using static Foo.Helpers;
using Alias = Shop.Domain.Entities.Host;
global using Shared.Things;
namespace Shop.Application.Hosts;
public class X {}
"""
    sf = CSharpExtractor(tmp_path).extract("src/A/X.cs", src)
    assert sf.provides == ["Shop.Application.Hosts"]
    assert targets(sf) == ["System", "Foo.Helpers", "Shop.Domain.Entities.Host", "Shared.Things"]
    assert sf.imports[0].line == 2


def test_csharp_block_namespace(tmp_path):
    sf = CSharpExtractor(tmp_path).extract("a.cs", b"namespace A.B { class C {} }")
    assert sf.provides == ["A.B"]


def test_python_relative_and_absolute(tmp_path):
    ex = PythonExtractor(tmp_path, source_roots=["src"])
    sf = ex.extract(
        "src/app/services/billing.py",
        b"import os.path\nfrom .helpers import fmt\nfrom ..domain import Invoice as Inv\nfrom app.adapters.db import repo\nfrom . import x\n",
    )
    assert sf.provides == ["app.services.billing"]
    assert targets(sf) == [
        "os.path",
        "app.services.helpers",
        "app.services.helpers.fmt",
        "app.domain",
        "app.domain.Invoice",
        "app.adapters.db",
        "app.adapters.db.repo",
        "app.services",
        "app.services.x",
    ]


def test_python_package_init(tmp_path):
    sf = PythonExtractor(tmp_path).extract("pkg/__init__.py", b"from .sub import thing\n")
    assert sf.provides == ["pkg"]
    assert "pkg.sub" in targets(sf)


def test_dart_resolution(tmp_path):
    (tmp_path / "pubspec.yaml").write_text("name: myapp\n")
    ex = DartExtractor(tmp_path)
    ex.prepare(["lib/presentation/a/a_screen.dart", "lib/core/di/locator.dart"])
    sf = ex.extract(
        "lib/presentation/a/a_screen.dart",
        b"import 'package:flutter/material.dart';\nimport '../../core/di/locator.dart';\nimport 'package:myapp/domain/entities/host.dart' as h;\nexport 'widgets.dart';\npart 'a_screen.g.dart';\n",
    )
    assert "package:myapp/presentation/a/a_screen.dart" in sf.provides
    assert targets(sf) == [
        "package:flutter/material.dart",
        "lib/core/di/locator.dart",
        "lib/domain/entities/host.dart",
        "lib/presentation/a/widgets.dart",
        "lib/presentation/a/a_screen.g.dart",
    ]


def test_rust_paths(tmp_path):
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "Cargo.toml").write_text('[package]\nname = "my-agent"\nversion = "0.1.0"\n\n[dependencies]\nname = "not-this"\n')
    ex = RustExtractor(tmp_path)
    ex.prepare(["agent/src/main.rs", "agent/src/remote/mod.rs", "agent/src/remote/ipc.rs"])
    sf = ex.extract(
        "agent/src/remote/ipc.rs",
        b"use crate::policy::{load, save as s};\nuse super::wire::Frame;\nuse std::io::{self, Write};\nuse self::inner::*;\nmod inner;\n",
    )
    assert sf.provides == ["my_agent::remote::ipc"]
    assert targets(sf) == [
        "my_agent::policy::load",
        "my_agent::policy::save",
        "my_agent::remote::wire::Frame",
        "std::io",
        "std::io::Write",
        "my_agent::remote::ipc::inner",
        "my_agent::remote::ipc::inner",
    ]
    main = ex.extract("agent/src/main.rs", b"mod remote;\n")
    assert main.provides == ["my_agent"]
    assert targets(main) == ["my_agent::remote"]
    modrs = ex.extract("agent/src/remote/mod.rs", b"pub mod ipc;")
    assert modrs.provides == ["my_agent::remote"]
    assert modrs.scope == "agent/src"


def test_rust_inline_test_module_super(tmp_path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "a"\n')
    ex = RustExtractor(tmp_path)
    ex.prepare(["src/main.rs", "src/queue.rs"])
    sf = ex.extract(
        "src/queue.rs",
        b"use crate::policy::Policy;\n#[cfg(test)]\nmod tests {\n    use super::*;\n    use super::super::config;\n    mod deeper { use super::super::helper; }\n}\n",
    )
    assert targets(sf) == ["a::policy::Policy", "a::queue", "a::config", "a::queue::helper"]
