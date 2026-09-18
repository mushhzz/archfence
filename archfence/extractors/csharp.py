"""C#: provides = declared namespaces; imports = using directives (plain, static, alias, global)."""
from __future__ import annotations

import re

from ..core.model import Import, Route, SourceFile, TypeDef
from .base import Extractor, count_members, text, walk

_NS_TYPES = {"namespace_declaration", "file_scoped_namespace_declaration"}
_HTTP_ATTRS = {"HttpGet": "GET", "HttpPost": "POST", "HttpPut": "PUT", "HttpPatch": "PATCH", "HttpDelete": "DELETE", "HttpHead": "HEAD", "HttpOptions": "OPTIONS"}
_STR = re.compile(r'^@?"(.*)"$', re.S)


def _attr_args(attr):
    """(first positional string or None, {Name: value-string}) for one attribute."""
    args = next((c for c in attr.children if c.type == "attribute_argument_list"), None)
    pos, named = None, {}
    if args is None:
        return pos, named
    for a in (c for c in args.children if c.type == "attribute_argument"):
        kids = [c for c in a.children if c.type not in (",", "=", ":")]
        if len(kids) >= 2 and kids[0].type in ("identifier", "name_equals", "name_colon"):
            key = text(kids[0]).rstrip("=: ").strip()
            m = _STR.match(text(kids[-1]).strip())
            named[key] = m.group(1) if m else text(kids[-1])
        elif kids and pos is None:
            m = _STR.match(text(kids[0]).strip())
            if m:
                pos = m.group(1)
    return pos, named


def _attrs(node):
    for al in (c for c in node.children if c.type == "attribute_list"):
        for attr in (c for c in al.children if c.type == "attribute"):
            name = next((c for c in attr.children if c.type in ("identifier", "qualified_name")), None)
            if name is not None:
                yield text(name).rsplit(".", 1)[-1], attr


def _qualified(node):
    """Return the qualified name inside a using/namespace node, or None."""
    for child in node.children:
        if child.type in ("qualified_name", "identifier", "alias_qualified_name"):
            return child
    return None


class CSharpExtractor(Extractor):
    language = "csharp"
    grammar = "csharp"
    extensions = (".cs",)

    def extract(self, rel_path: str, source: bytes) -> SourceFile:
        tree = self.parse(rel_path, source)
        sf = SourceFile(path=rel_path, language=self.language)
        sf.parse_error = tree.root_node.has_error
        for ns in walk(tree.root_node, _NS_TYPES):
            q = _qualified(ns)
            if q is not None:
                name = text(q).replace(" ", "")
                if name not in sf.provides:
                    sf.provides.append(name)
        for using in walk(tree.root_node, {"using_directive"}):
            # Alias form: `using X = A.B.C;` -> the qualified name after '='.
            names = [c for c in using.children if c.type in ("qualified_name", "identifier", "alias_qualified_name")]
            if not names:
                continue
            target_node = names[-1]  # for alias form the last name is the real target
            target = text(target_node).replace(" ", "")
            if target.startswith("global::"):
                target = target[len("global::"):]
            sf.imports.append(Import(target=target, line=using.start_point[0] + 1, raw=text(using).strip()))
        sf.routes = self._routes(tree)
        sf.types = self._types(tree)
        return sf

    def _routes(self, tree) -> list[Route]:
        """ASP.NET attribute routing: `[Route("api/[controller]")]` on the class, `[HttpGet("{id}", Name = "...")]` on methods."""
        routes: list[Route] = []
        for cls in walk(tree.root_node, {"class_declaration"}):
            cls_name = text(cls.child_by_field_name("name")) if cls.child_by_field_name("name") is not None else ""
            prefixes = [p for name, attr in _attrs(cls) if name == "Route" for p in [_attr_args(attr)[0]] if p is not None]
            controller = cls_name[: -len("Controller")] if cls_name.endswith("Controller") else cls_name
            prefixes = [p.replace("[controller]", controller.lower()) for p in prefixes] or [""]
            body = next((c for c in cls.children if c.type == "declaration_list"), None)
            if body is None:
                continue
            for m in (c for c in body.children if c.type == "method_declaration"):
                handler = text(m.child_by_field_name("name")) if m.child_by_field_name("name") is not None else ""
                for name, attr in _attrs(m):
                    if name not in _HTTP_ATTRS:
                        continue
                    tmpl, named = _attr_args(attr)
                    op = named.get("Name")
                    for pre in prefixes:
                        path = _join_route(pre, tmpl or "")
                        routes.append(Route(_HTTP_ATTRS[name], path, attr.start_point[0] + 1, op, handler))
        return routes

    def _types(self, tree) -> list[TypeDef]:
        out: list[TypeDef] = []
        for cd in walk(tree.root_node, {"class_declaration", "struct_declaration", "record_declaration"}):
            name = cd.child_by_field_name("name")
            methods = count_members(cd, {"declaration_list"}, {"method_declaration", "constructor_declaration"})
            out.append(TypeDef(text(name) if name is not None else "?", cd.type.split("_")[0], methods, cd.start_point[0] + 1))
        return out


def _join_route(prefix: str, template: str) -> str:
    if template.startswith("/") or template.startswith("~/"):
        return "/" + template.lstrip("~/")
    parts = [p for p in (prefix.strip("/"), template.strip("/")) if p]
    return "/" + "/".join(parts)

