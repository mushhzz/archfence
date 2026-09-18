import textwrap
from pathlib import Path

import pytest

from archfence.checks.contracts import normalise_path
from archfence.cli import main
from archfence.core.config import ConfigError, load_config
from conftest import write


def test_normalise_path():
    assert normalise_path("/users/{user_id}/") == "/users/{}"
    assert normalise_path("users/<int:id>") == "/users/{}"
    assert normalise_path("/users/:id") == "/users/{}"
    assert normalise_path("/") == "/"


def test_contract_checks(api, monkeypatch, capsys):
    monkeypatch.chdir(api)
    assert main(["scan"]) == 1
    out = capsys.readouterr().out
    assert "app/schemas/bad.py:1: error[contract-depends-on-code] contract layer 'schemas' must not depend on layer 'models'" in out
    assert "app/routers/raw.py:4: error[route-without-contract] 1 route(s) declared but nothing imported from contract layer 'schemas'" in out
    # health is ignored on both sides, so no route-without-contract for it either? It declares a route and imports nothing:
    assert "app/routers/health.py" in out  # route-without-contract still applies; ignore only affects the spec comparison
    assert "app/routers/users.py:10: warning[route-contract-mismatch] operation 'resetUser' is POST /api/v1/users/{}/reset in code but POST /api/v1/users/{}/reset-password in docs/openapi.yaml" in out
    # contract first: a spec entry with no code is pending work, not drift
    assert "docs/openapi.yaml:1: info[contract-pending] POST /api/v1/users (createUser) is in docs/openapi.yaml and not implemented yet" in out
    assert "listUsers" not in out and "getUser" not in out and "deleteUser" not in out and "rawRows" not in out
    assert "FAIL: 3 error(s) (1 warning(s), 1 info)" in out


def test_contract_route_missing_from_spec(api, monkeypatch, capsys):
    monkeypatch.chdir(api)
    write(api, "app/routers/extra.py", "from app.schemas.user import User\nrouter = APIRouter()\n@router.put('/extra', operation_id='putExtra')\ndef e(): ...\n")
    main(["scan"])
    out = capsys.readouterr().out
    assert "app/routers/extra.py:3: warning[route-not-in-contract] PUT /api/v1/extra (e) is not in docs/openapi.yaml: no operation_id 'putExtra'" in out


def test_routes_command(api, monkeypatch, capsys):
    monkeypatch.chdir(api)
    assert main(["routes"]) == 0
    out = capsys.readouterr().out
    assert "6 route(s), prefix /api/v1" in out
    assert "GET      /api/v1/users/{user_id}" in out and "op=getUser" in out


def test_contract_config_validation(tmp_path):
    write(tmp_path, "archfence.yml", "languages: [python]\nlayers:\n  a: {paths: ['a/**']}\ncontracts: {layer: nope}\n")
    with pytest.raises(ConfigError, match="contracts.layer must name one of the layers"):
        load_config(tmp_path / "archfence.yml")
    write(tmp_path, "archfence.yml", "languages: [python]\nlayers:\n  a: {paths: ['a/**']}\nslices: {shared: ['x']}\n")
    with pytest.raises(ConfigError, match="slices needs 'roots'"):
        load_config(tmp_path / "archfence.yml")


def test_generated_spec_telltale(api, monkeypatch, capsys):
    monkeypatch.chdir(api)
    spec = (api / "docs/openapi.yaml").read_text()
    write(api, "docs/openapi.yaml", "info: {title: FastAPI, version: 0.1.0}\ncomponents:\n  schemas:\n    HTTPValidationError: {}\n    ValidationError: {}\n" + spec.replace("openapi: 3.0.0\n", ""))
    main(["scan"])
    out = capsys.readouterr().out
    assert "docs/openapi.yaml:1: warning[openapi-generated] docs/openapi.yaml looks generated from the code, not authored first: info.title is the framework default 'FastAPI'; components.schemas carries FastAPI's HTTPValidationError/ValidationError pair" in out


def _git(repo: Path, *args, date: str = "2026-01-01T00:00:00"):
    import os
    import subprocess

    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(repo)}
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args], check=True, capture_output=True, env=env)


def test_history_orders_contract_before_code(tmp_path, monkeypatch, capsys):
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/schemas/u.py", "class U: ...\n")
    write(tmp_path, "archfence.yml", """
        languages: [python]
        layers:
          routers: { paths: ["app/routers/**"] }
          schemas: { paths: ["app/schemas/**"] }
        contracts:
          layer: schemas
          consumers: [routers]
          openapi: { path: openapi.yaml, prefix: /api, history: true }
    """)
    _git(tmp_path, "init", "-q")
    # day 1: contract for listUsers agreed; code for getUser written with no contract
    write(tmp_path, "openapi.yaml", "openapi: 3.0.0\ninfo: {title: Users}\npaths:\n  /api/users:\n    get: {operationId: listUsers}\n")
    write(tmp_path, "app/routers/u.py", "from app.schemas.u import U\nrouter = APIRouter()\n@router.get('/users/{id}', operation_id='getUser')\ndef g(): ...\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "day1", date="2026-01-01T00:00:00")
    # day 5: listUsers implemented (contract first, fine); getUser back-filled into the spec (code first, flagged)
    write(tmp_path, "app/routers/u.py", "from app.schemas.u import U\nrouter = APIRouter()\n@router.get('/users/{id}', operation_id='getUser')\ndef g(): ...\n@router.get('/users', operation_id='listUsers')\ndef l(): ...\n")
    write(tmp_path, "openapi.yaml", "openapi: 3.0.0\ninfo: {title: Users}\npaths:\n  /api/users:\n    get: {operationId: listUsers}\n  /api/users/{id}:\n    get: {operationId: getUser}\n")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "day5", date="2026-01-05T00:00:00")
    monkeypatch.chdir(tmp_path)
    assert main(["scan"]) == 0
    out = capsys.readouterr().out
    assert "app/routers/u.py:3: warning[route-before-contract] 'getUser' reached code in " in out
    assert "4 day(s) before it reached openapi.yaml" in out
    assert "listUsers" not in out
    assert "0 error(s), 1 warning(s)" in out


def test_history_code_op_matcher_is_attribute_scoped():
    from archfence.checks.contracts.history import _match_code_op

    # Python / JS route decorators
    assert _match_code_op('+    @router.get("/x", operation_id="listUsers")').group(1) == "listUsers"
    # C#: Name on a routing attribute is a route id
    assert _match_code_op('+        [HttpGet("{id}", Name = "GetUser")]').group(1) == "GetUser"
    assert _match_code_op('+    [Route("api/v1/orders", Name="Orders")]').group(1) == "Orders"
    # C#: an ordinary Name assignment is NOT a route id (the old regex matched this)
    assert _match_code_op('+        public string Name = "Widget";') is None
    assert _match_code_op('+    var opts = new Options { Name = "cache" };') is None
    assert _match_code_op('+    logger.LogInformation("done");') is None
