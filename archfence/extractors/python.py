"""Python: provides = dotted module path(s); imports = absolute dotted names (relative ones resolved)."""
from __future__ import annotations

from pathlib import PurePosixPath

from ..core.model import Import, Route, SourceFile, TypeDef
from .base import Extractor, count_members, text, walk

_HTTP = {"get", "post", "put", "patch", "delete", "head", "options"}
_ROUTE_FUNCS = _HTTP | {"route", "api_route", "add_api_route", "websocket"}


def _string(node) -> str | None:
    if node is None or node.type not in ("string", "concatenated_string"):
        return None
    return "".join(text(c) for c in walk(node, {"string_content"}))


def _kwargs(arglist) -> dict:
    out = {}
    for c in arglist.children:
        if c.type == "keyword_argument":
            k = c.child_by_field_name("name")
            v = c.child_by_field_name("value")
            if k is not None:
                out[text(k)] = v
    return out


def _positional(arglist):
    return [c for c in arglist.children if c.type not in ("(", ")", ",", "keyword_argument", "comment")]


class PythonExtractor(Extractor):
    language = "python"
    grammar = "python"
    extensions = (".py",)

    def _module_names(self, rel_path: str) -> list[str]:
        """Compute dotted module names for a file, one per matching source root."""
        p = PurePosixPath(rel_path)
        roots = self.source_roots or ["."]
        names: list[str] = []
        for root in roots:
            root = root.strip("/")
            parts = list(p.parts)
            if root and root != ".":
                rparts = root.split("/")
                if parts[: len(rparts)] != rparts:
                    continue
                parts = parts[len(rparts):]
            if not parts:
                continue
            parts[-1] = parts[-1][: -len(".py")]
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts:
                names.append(".".join(parts))
        return names or [".".join(p.with_suffix("").parts)]

    def extract(self, rel_path: str, source: bytes) -> SourceFile:
        tree = self.parse(rel_path, source)
        sf = SourceFile(path=rel_path, language=self.language)
        sf.parse_error = tree.root_node.has_error
        sf.provides = self._module_names(rel_path)
        primary = sf.provides[0]
        is_package = PurePosixPath(rel_path).name == "__init__.py"
        package = primary if is_package else primary.rpartition(".")[0]

        for node in walk(tree.root_node, {"import_statement", "import_from_statement"}):
            line = node.start_point[0] + 1
            raw = text(node).strip()
            if node.type == "import_statement":
                for child in node.children:
                    if child.type == "dotted_name":
                        sf.imports.append(Import(text(child), line, raw))
                    elif child.type == "aliased_import":
                        dn = child.child_by_field_name("name")
                        if dn is not None:
                            sf.imports.append(Import(text(dn), line, raw))
                continue

            module_node = node.child_by_field_name("module_name")
            base = ""
            if module_node is not None and module_node.type == "relative_import":
                dots = 0
                tail = ""
                for c in module_node.children:
                    if c.type == "import_prefix":
                        dots = len(text(c))
                    elif c.type == "dotted_name":
                        tail = text(c)
                parts = package.split(".") if package else []
                if dots > 1:
                    parts = parts[: len(parts) - (dots - 1)] if dots - 1 <= len(parts) else []
                base = ".".join(parts)
                if tail:
                    base = f"{base}.{tail}" if base else tail
            elif module_node is not None:
                base = text(module_node)

            names = [c for c in node.children if c.type in ("dotted_name", "aliased_import", "wildcard_import")]
            # first dotted_name child is the module itself when not relative
            imported: list[str] = []
            for c in names:
                if module_node is not None and c.start_byte == module_node.start_byte:
                    continue
                if c.type == "wildcard_import":
                    continue
                if c.type == "aliased_import":
                    dn = c.child_by_field_name("name")
                    if dn is not None:
                        imported.append(text(dn))
                else:
                    imported.append(text(c))
            if base:
                sf.imports.append(Import(base, line, raw))
            for name in imported:
                full = f"{base}.{name}" if base else name
                sf.imports.append(Import(full, line, raw))
        sf.routes = self._routes(tree)
        sf.types = self._types(tree)
        return sf

    def _routes(self, tree) -> list[Route]:
        """FastAPI / Starlette / Flask style: `@x.get("/p", operation_id=...)`, `@bp.route("/p", methods=[...])`.
        A module-level `x = APIRouter(prefix="/p")` or `Blueprint(..., url_prefix=...)` is prepended."""
        prefixes: dict[str, str] = {}
        for asg in walk(tree.root_node, {"assignment"}):
            left, right = asg.child_by_field_name("left"), asg.child_by_field_name("right")
            if left is None or right is None or right.type != "call" or left.type != "identifier":
                continue
            fn = right.child_by_field_name("function")
            args = right.child_by_field_name("arguments")
            if fn is None or args is None or text(fn).rsplit(".", 1)[-1] not in ("APIRouter", "Blueprint", "Router"):
                continue
            kw = _kwargs(args)
            pre = _string(kw.get("prefix")) or _string(kw.get("url_prefix"))
            if pre:
                prefixes[text(left)] = pre
        routes: list[Route] = []
        for dd in walk(tree.root_node, {"decorated_definition"}):
            fn_def = dd.child_by_field_name("definition")
            handler = text(fn_def.child_by_field_name("name")) if fn_def is not None and fn_def.child_by_field_name("name") is not None else ""
            for dec in (c for c in dd.children if c.type == "decorator"):
                call = next((c for c in dec.children if c.type == "call"), None)
                if call is None:
                    continue
                fn = call.child_by_field_name("function")
                args = call.child_by_field_name("arguments")
                if fn is None or args is None or fn.type != "attribute":
                    continue
                obj, meth = text(fn.child_by_field_name("object")), text(fn.child_by_field_name("attribute"))
                if meth not in _ROUTE_FUNCS:
                    continue
                pos = _positional(args)
                path = _string(pos[0]) if pos else None
                if path is None:
                    continue
                kw = _kwargs(args)
                methods = [meth.upper()] if meth in _HTTP else []
                if not methods and "methods" in kw and kw["methods"].type in ("list", "tuple"):
                    methods = [m.upper() for m in (_string(c) for c in kw["methods"].children) if m]
                if not methods:
                    methods = ["*"] if meth != "websocket" else ["WEBSOCKET"]
                op = _string(kw.get("operation_id")) or _string(kw.get("name"))
                full = (prefixes.get(obj, "") + path) or "/"
                for m in methods:
                    routes.append(Route(m, full, dec.start_point[0] + 1, op, handler))
        return routes

    def _types(self, tree) -> list[TypeDef]:
        out: list[TypeDef] = []
        for cd in walk(tree.root_node, {"class_definition"}):
            name = cd.child_by_field_name("name")
            methods = count_members(cd, {"block"}, {"function_definition"})
            out.append(TypeDef(text(name) if name is not None else "?", "class", methods, cd.start_point[0] + 1))
        return out

