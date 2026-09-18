"""Rust: provides = crate-qualified module paths; imports = `use` paths and `mod` declarations."""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from ..core.model import Import, SourceFile, TypeDef
from .base import Extractor, count_members, text, walk

_PKG_RE = re.compile(r'^\s*name\s*=\s*"([^"]+)"', re.M)


class RustExtractor(Extractor):
    language = "rust"
    grammar = "rust"
    extensions = (".rs",)

    def __init__(self, root: Path, source_roots=None):
        super().__init__(root, source_roots)
        self.crates: dict[str, str] = {}  # src dir (posix, relative) -> crate name

    def prepare(self, rel_paths: list[str]) -> None:
        seen: set[str] = set()
        for rp in rel_paths:
            d = str(PurePosixPath(rp).parent)
            while d not in seen:
                seen.add(d)
                cargo = self.root / d / "Cargo.toml"
                if cargo.exists():
                    txt = cargo.read_text(encoding="utf-8", errors="replace")
                    pkg_section = txt.split("[package]", 1)
                    if len(pkg_section) == 2:
                        m = _PKG_RE.search(pkg_section[1].split("\n[", 1)[0])
                        if m:
                            src = str(PurePosixPath(d) / "src") if d != "." else "src"
                            self.crates[src] = m.group(1).replace("-", "_")
                    break
                if d in (".", ""):
                    break
                d = str(PurePosixPath(d).parent)

    def _module_path(self, rel_path: str) -> tuple[str, str, list[str]] | None:
        """Return (src dir, crate, module segments) for a file, or None if not inside a known crate."""
        for src, crate in sorted(self.crates.items(), key=lambda kv: -len(kv[0])):
            prefix = src.rstrip("/") + "/"
            if rel_path.startswith(prefix):
                parts = list(PurePosixPath(rel_path[len(prefix):]).parts)
                parts[-1] = parts[-1][: -len(".rs")]
                if parts[-1] in ("mod", "lib", "main"):
                    parts = parts[:-1]
                # bin targets: src/bin/foo.rs is its own root module
                if parts[:1] == ["bin"]:
                    parts = parts[1:] and [f"bin::{parts[1]}"] + parts[2:]
                return src, crate, parts
        return None

    def extract(self, rel_path: str, source: bytes) -> SourceFile:
        tree = self.parse(rel_path, source)
        sf = SourceFile(path=rel_path, language=self.language)
        sf.parse_error = tree.root_node.has_error
        mp = self._module_path(rel_path)
        if mp is None:
            crate, segs = PurePosixPath(rel_path).parts[0], list(PurePosixPath(rel_path).with_suffix("").parts[1:])
            sf.scope = crate
        else:
            sf.scope, crate, segs = mp
        own = "::".join([crate] + segs)
        sf.provides = [own]

        def inline_mods(node) -> list[str]:
            """Names of the inline `mod x { ... }` blocks enclosing ``node``, outermost first."""
            names: list[str] = []
            cur = node.parent
            while cur is not None:
                if cur.type == "mod_item" and any(c.type == "declaration_list" for c in cur.children):
                    name = cur.child_by_field_name("name")
                    if name is not None:
                        names.append(text(name))
                cur = cur.parent
            return list(reversed(names))

        def abs_path(raw: str, here: list[str]) -> str | None:
            raw = raw.replace(" ", "")
            head, _, rest = raw.partition("::")
            if head == "crate":
                return crate + ("::" + rest if rest else "")
            if head == "self":
                return "::".join([crate] + here) + ("::" + rest if rest else "")
            if head == "super":
                cur = here[:-1]
                while rest.startswith("super"):
                    cur = cur[:-1]
                    rest = rest.partition("::")[2]
                base = "::".join([crate] + cur)
                return base + ("::" + rest if rest else "")
            return raw  # external crate or a sibling crate by name

        for use in walk(tree.root_node, {"use_declaration"}):
            line = use.start_point[0] + 1
            raw = text(use).strip()
            here = segs + inline_mods(use)
            for path in _expand_use(use):
                target = abs_path(path, here)
                if target:
                    sf.imports.append(Import(target, line, raw))
        for mod in walk(tree.root_node, {"mod_item"}):
            if not any(c.type == "declaration_list" for c in mod.children):
                name = mod.child_by_field_name("name")
                if name is not None:
                    here = "::".join([crate] + segs + inline_mods(mod))
                    sf.imports.append(Import(f"{here}::{text(name)}", mod.start_point[0] + 1, text(mod).strip()))
        sf.types = self._types(tree)
        return sf

    def _types(self, tree) -> list[TypeDef]:
        """Rust has no class; the size unit is an impl block (methods on a type) or a trait's method set."""
        out: list[TypeDef] = []
        for it in walk(tree.root_node, {"impl_item", "trait_item"}):
            methods = count_members(it, {"declaration_list"}, {"function_item", "function_signature_item"})
            name = it.child_by_field_name("name")
            if name is None:  # impl blocks carry the type as a plain type_identifier, not a name field
                name = next((c for c in it.children if c.type in ("type_identifier", "generic_type", "scoped_type_identifier")), None)
            kind = "trait" if it.type == "trait_item" else "impl"
            out.append(TypeDef(text(name) if name is not None else "?", kind, methods, it.start_point[0] + 1))
        return out


def _expand_use(use_node) -> list[str]:
    """Flatten a `use` declaration's argument into a list of plain `a::b::c` paths."""
    arg = use_node.child_by_field_name("argument")
    if arg is None:
        return []
    out: list[str] = []

    def rec(node, prefix: str):
        t = node.type
        if t in ("identifier", "crate", "self", "super", "metavariable"):
            out.append(_join(prefix, text(node)))
        elif t == "scoped_identifier":
            path = node.child_by_field_name("path")
            name = node.child_by_field_name("name")
            base = _join(prefix, text(path)) if path is not None else prefix
            out.append(_join(base, text(name)) if name is not None else base)
        elif t == "use_as_clause":
            path = node.child_by_field_name("path")
            if path is not None:
                rec(path, prefix)
        elif t == "scoped_use_list":
            path = node.child_by_field_name("path")
            lst = node.child_by_field_name("list")
            base = _join(prefix, text(path)) if path is not None else prefix
            if lst is not None:
                rec(lst, base)
        elif t == "use_list":
            for c in node.children:
                if c.type not in ("{", "}", ","):
                    rec(c, prefix)
        elif t == "use_wildcard":
            path = next((c for c in node.children if c.type not in ("::", "*")), None)
            if path is not None:
                rec(path, prefix)
            else:
                out.append(prefix)
        else:
            out.append(_join(prefix, text(node)))

    rec(arg, "")
    return [o for o in out if o]


def _join(prefix: str, name: str) -> str:
    name = name.replace(" ", "")
    if not prefix:
        return name
    if name == "self":
        return prefix
    return f"{prefix}::{name}"
