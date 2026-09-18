import pytest

from archfence.cli import main
from archfence.core.config import ConfigError, load_config
from conftest import write


def _proj(tmp_path, extra_yaml, routed=False):
    write(tmp_path, "app/util.py", "x = 1\n")
    for m in ("a", "b", "c"):
        write(tmp_path, f"app/{m}.py", "from app.util import x\n")
    write(tmp_path, "app/hub.py", "from app.a import *\nfrom app.b import *\nfrom app.c import *\n")
    write(tmp_path, "app/main.py", "def main():\n    pass\n")
    if routed:
        write(tmp_path, "app/api.py", "router = APIRouter()\n@router.get('/x', operation_id='x')\ndef x(): ...\n")
    write(tmp_path, "archfence.yml", "languages: [python]\nsource_roots: [.]\nlayers:\n  app: { paths: [\"app/**\"] }\n" + extra_yaml)
    return tmp_path


def test_fan_out_and_fan_in_are_gate_able(tmp_path, monkeypatch, capsys):
    _proj(tmp_path, "metrics:\n  fan_out: { max: 2, severity: error }\n  fan_in: 2\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/hub.py:1: error[high-fan-out]" in out and "depends on 3 project files" in out
    assert "app/util.py:1: warning[high-fan-in]" in out and "depended on by 3 project files" in out


def test_metrics_thresholds_respect_strict_and_waivers(tmp_path, monkeypatch, capsys):
    _proj(tmp_path, "strict: true\nmetrics:\n  fan_in: { max: 2, severity: warning }\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1  # strict promotes the fan-in warning to an error
    assert "error[high-fan-in]" in capsys.readouterr().out

    write(tmp_path, "archfence.yml", "languages: [python]\nsource_roots: [.]\nlayers:\n  app: { paths: [\"app/**\"] }\n"
          "metrics:\n  fan_in: { max: 2, severity: warning }\n"
          "allow:\n  - { path: app/util.py, rules: [high-fan-in], reason: shared kernel }\n")
    assert main(["scan"]) == 0
    assert "high-fan-in" not in capsys.readouterr().out


def test_dead_code_honours_entrypoints_and_routes(tmp_path, monkeypatch, capsys):
    write(tmp_path, "app/orphan.py", "y = 2\n")
    _proj(tmp_path, "metrics:\n  dead_code:\n    severity: warning\n    entrypoints: [\"app/main.py\"]\n", routed=True)
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "app/orphan.py:1: warning[dead-code]" in out
    assert "app/main.py" not in out  # declared entrypoint
    assert "app/api.py" not in out   # a route-declaring file is a framework entrypoint
    assert "app/util.py" not in out  # imported by a, b, c


def test_metrics_command_reports_coupling(tmp_path, monkeypatch, capsys):
    _proj(tmp_path, "")
    monkeypatch.chdir(tmp_path)
    assert main(["metrics"]) == 0
    out = capsys.readouterr().out
    assert "highest fan-out" in out and "highest fan-in" in out and "instability" in out
    assert "app/util.py" in out


def test_couplings_and_instability_are_correct(tmp_path):
    from archfence.core.graph import build_graph
    from archfence.checks.metrics import couplings, instability

    _proj(tmp_path, "")
    project = load_config(tmp_path / "archfence.yml").projects[0]
    graph = build_graph(tmp_path, project)
    eff, aff = couplings(graph)
    assert len(aff["app/util.py"]) == 3 and len(eff["app/util.py"]) == 0
    assert len(eff["app/hub.py"]) == 3
    assert instability(3, 0) == 1.0 and instability(0, 3) == 0.0 and instability(0, 0) == 0.0


def test_unknown_metrics_key_is_rejected(tmp_path):
    _proj(tmp_path, "metrics:\n  fanout: 5\n")
    with pytest.raises(ConfigError, match="unknown key"):
        load_config(tmp_path / "archfence.yml")


def test_god_class_is_gate_able(tmp_path, monkeypatch, capsys):
    write(tmp_path, "app/big.py", "class Big:\n" + "".join(f"    def m{i}(self): pass\n" for i in range(6)))
    write(tmp_path, "app/small.py", "class Small:\n    def a(self): pass\n")
    write(tmp_path, "archfence.yml", "languages: [python]\nsource_roots: [.]\nlayers:\n  app: { paths: [\"app/**\"] }\n"
          "metrics:\n  god_class: { max_methods: 5, severity: error }\n")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/big.py:1: error[god-class]" in out and "has 6 methods" in out
    assert "Small" not in out


def test_type_extraction_counts_methods_per_language(tmp_path):
    from pathlib import Path
    from archfence.extractors.csharp import CSharpExtractor
    from archfence.extractors.rust import RustExtractor
    from archfence.extractors.typescript import TypeScriptExtractor
    from archfence.extractors.dart import DartExtractor

    cs = CSharpExtractor(Path(".")).extract("m.cs", b"namespace N; class Foo { void A(){} Foo(){} int X {get;set;} }")
    assert (cs.types[0].name, cs.types[0].methods) == ("Foo", 2)  # method + ctor, not the property
    ts = TypeScriptExtractor(Path(".")).extract("m.ts", b"export class Foo { a(){} b(){} c=1; }")
    assert (ts.types[0].name, ts.types[0].methods) == ("Foo", 2)  # field not counted
    rs = RustExtractor(Path(".")).extract("src/m.rs", b"struct Foo; impl Foo { fn a(&self){} fn b(&self){} }")
    assert rs.types[0].methods == 2 and rs.types[0].kind == "impl"
    dt = DartExtractor(Path(".")).extract("lib/m.dart", b"class Foo { void a(){} int b()=>1; int c=0; }")
    assert (dt.types[0].name, dt.types[0].methods) == ("Foo", 2)
