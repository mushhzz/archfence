import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def write(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))


@pytest.fixture
def csharp_project(tmp_path):
    write(tmp_path, "src/App.Domain/Entities/Host.cs", "namespace App.Domain.Entities;\npublic class Host {}\n")
    write(tmp_path, "src/App.Domain/Bad.cs", "using Microsoft.EntityFrameworkCore;\nnamespace App.Domain;\n")
    write(tmp_path, "src/App.Application/Hosts/Get.cs", "using App.Domain.Entities;\nusing MediatR;\nnamespace App.Application.Hosts;\n")
    write(tmp_path, "src/App.Application/Leak.cs", "using App.Infrastructure.Persistence;\nnamespace App.Application;\n")
    write(tmp_path, "src/App.Infrastructure/Persistence/Db.cs", "using App.Application.Hosts;\nusing App.Domain.Entities;\nnamespace App.Infrastructure.Persistence;\n")
    write(tmp_path, "src/App.WebApi/C.cs", "using App.Application.Hosts;\nusing App.Infrastructure.Persistence;\nnamespace App.WebApi;\n")
    write(tmp_path, "archfence.yml", """
        extends: clean-architecture-dotnet
        layers:
          domain: { paths: ["src/App.Domain/**"] }
          application: { paths: ["src/App.Application/**"] }
          infrastructure: { paths: ["src/App.Infrastructure/**"] }
          webapi: { paths: ["src/App.WebApi/**"] }
    """)
    return tmp_path


@pytest.fixture
def leaky_project(tmp_path):
    write(tmp_path, "app/domain/m.py", "import sqlalchemy\n")
    write(tmp_path, "app/services/s.py", "from app.adapters.db import x\nimport requests\n")
    write(tmp_path, "app/adapters/db.py", "x = 1\n")
    return tmp_path


@pytest.fixture
def sliced(tmp_path):
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/shared/db.py", "x = 1\n")
    write(tmp_path, "app/shared/leak.py", "from app.features.billing.service import bill\n")
    write(tmp_path, "app/features/billing/service.py", "from app.shared.db import x\n")
    write(tmp_path, "app/features/billing/api.py", "from .service import bill\nfrom app.features.users.repo import find\n")
    write(tmp_path, "app/features/users/repo.py", "from app.shared.db import x\n")
    write(tmp_path, "app/features/reports/build.py", "from app.features.users.repo import find\nfrom app.legacy.util import u\n")
    write(tmp_path, "app/legacy/util.py", "u = 1\n")
    return tmp_path


@pytest.fixture
def api(tmp_path):
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/schemas/user.py", "class User: ...\n")
    write(tmp_path, "app/schemas/bad.py", "from app.models.user import UserRow\n")
    write(tmp_path, "app/models/user.py", "UserRow = 1\n")
    write(tmp_path, "app/routers/users.py", """
        router = APIRouter(prefix="/users")
        from app.schemas.user import User
        @router.get("", operation_id="listUsers")
        def list_users(): ...
        @router.get("/{user_id}", operation_id="getUser")
        def get_user(): ...
        @router.delete("/{user_id}", operation_id="deleteUser")
        def delete_user(): ...
        @router.post("/{user_id}/reset", operation_id="resetUser")
        def reset(): ...
    """)
    write(tmp_path, "app/routers/health.py", """
        router = APIRouter()
        @router.get("/health")
        def health(): ...
    """)
    write(tmp_path, "app/routers/raw.py", """
        router = APIRouter(prefix="/raw")
        from app.models.user import UserRow
        @router.get("/rows", operation_id="rawRows")
        def rows(): ...
    """)
    write(tmp_path, "docs/openapi.yaml", """
        openapi: 3.0.0
        paths:
          /api/v1/users:
            get: { operationId: listUsers }
            post: { operationId: createUser }
          /api/v1/users/{id}:
            get: { operationId: getUser }
            delete: { operationId: deleteUser }
          /api/v1/users/{id}/reset-password:
            post: { operationId: resetUser }
          /api/v1/raw/rows:
            get: { operationId: rawRows }
    """)
    write(tmp_path, "archfence.yml", """
        languages: [python]
        layers:
          routers: { paths: ["app/routers/**"] }
          schemas: { paths: ["app/schemas/**"] }
          models:  { paths: ["app/models/**"] }
        contracts:
          layer: schemas
          consumers: [routers]
          openapi:
            path: docs/openapi.yaml
            prefix: /api/v1
            match: both
            ignore: ["/health*"]
    """)
    return tmp_path
