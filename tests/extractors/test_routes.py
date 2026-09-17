import textwrap
from pathlib import Path

import pytest

from archfence.extractors.csharp import CSharpExtractor
from archfence.extractors.python import PythonExtractor
from conftest import write


def test_python_routes(tmp_path):
    sf = PythonExtractor(tmp_path).extract(
        "r.py",
        b'router = APIRouter(prefix="/settings")\n@router.get("/summary", operation_id="getSum")\ndef f(): ...\n'
        b'@bp.route("/a", methods=["GET", "POST"])\ndef g(): ...\n@router.websocket("/ws")\nasync def w(): ...\n@other\ndef h(): ...\n',
    )
    assert [(r.method, r.path, r.operation_id, r.handler, r.line) for r in sf.routes] == [
        ("GET", "/settings/summary", "getSum", "f", 2),
        ("GET", "/a", None, "g", 4),
        ("POST", "/a", None, "g", 4),
        ("WEBSOCKET", "/settings/ws", None, "w", 6),
    ]


def test_csharp_routes(tmp_path):
    sf = CSharpExtractor(tmp_path).extract(
        "c.cs",
        b'[ApiController]\n[Route("api/[controller]")]\npublic class HostController : ControllerBase {\n'
        b'  [HttpGet("{id}", Name = "GetHost")]\n  public IActionResult Get(int id) => Ok();\n  [HttpPost]\n  public void Post() {}\n  [HttpDelete("/absolute/{id}")] public void D(){}\n}\n',
    )
    assert [(r.method, r.path, r.operation_id) for r in sf.routes] == [
        ("GET", "/api/host/{id}", "GetHost"),
        ("POST", "/api/host", None),
        ("DELETE", "/absolute/{id}", None),
    ]
