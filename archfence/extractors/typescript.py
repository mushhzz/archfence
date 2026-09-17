"""TypeScript / JavaScript: provides = extension-less path (and its directory for index files);
imports = ES imports/re-exports, require() and dynamic import(), resolved through tsconfig `paths`."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from ..core.model import Import, SourceFile
from .base import Extractor, parser_for, text, walk

_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_INDEX = tuple(f"index{e}" for e in _EXTS)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments without touching string contents (tsconfig is JSON-with-comments,
    and `"@/*"` / `"**/*.ts"` look exactly like comment delimiters to a regex)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _load_jsonc(path: Path) -> dict:
    return json.loads(_TRAILING_COMMA.sub(r"\1", _strip_jsonc(path.read_text(encoding="utf-8")))) or {}


def _strip_ext(p: str) -> str:
    for e in _EXTS + (".d.ts",):
        if p.endswith(e):
            return p[: -len(e)]
    return p


def _norm(parts) -> str:
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        elif part not in (".", ""):
            out.append(part)
    return "/".join(out)


class TsConfig:
    def __init__(self, dir_rel: str, base_url: str, paths: dict[str, list[str]]):
        self.dir = dir_rel  # posix, relative to project root; "" for the root
        self.base = _norm((PurePosixPath(dir_rel) / base_url).parts) if base_url else dir_rel
        self.paths = paths

    def resolve(self, spec: str) -> str | None:
        """Map an aliased specifier such as `@/lib/x` onto a project-relative path, or None."""
        for pattern, targets in self.paths.items():
            if not targets:
                continue
            if "*" in pattern:
                head, tail = pattern.split("*", 1)
                if spec.startswith(head) and spec.endswith(tail):
                    star = spec[len(head) : len(spec) - len(tail)] if tail else spec[len(head):]
                    return _norm((PurePosixPath(self.base) / targets[0].replace("*", star)).parts)
            elif spec == pattern:
                return _norm((PurePosixPath(self.base) / targets[0]).parts)
        if self.paths and not spec.startswith((".", "/")) and self.base and self.base != self.dir:
            # baseUrl lets bare specifiers resolve from it too
            return _norm((PurePosixPath(self.base) / spec).parts)
        return None


class TypeScriptExtractor(Extractor):
    language = "typescript"
    grammar = "tsx"
    extensions = _EXTS

    def __init__(self, root: Path, source_roots=None):
        super().__init__(root, source_roots)
        self.configs: dict[str, TsConfig] = {}  # dir -> config

    def wants(self, rel_path: str) -> bool:
        return rel_path.endswith(self.extensions) and "node_modules/" not in rel_path

    def prepare(self, rel_paths: list[str]) -> None:
        seen: set[str] = set()
        for rp in rel_paths:
            d = str(PurePosixPath(rp).parent)
            while d not in seen:
                seen.add(d)
                for name in ("tsconfig.json", "jsconfig.json"):
                    cfg = self.root / d / name
                    if cfg.exists():
                        self._load(d if d != "." else "", cfg)
                        break
                if d in (".", ""):
                    break
                d = str(PurePosixPath(d).parent)

    def _load(self, dir_rel: str, cfg: Path, depth: int = 0) -> None:
        if dir_rel in self.configs or depth > 5:
            return
        try:
            data = _load_jsonc(cfg)
        except (OSError, json.JSONDecodeError):
            return
        opts = data.get("compilerOptions") or {}
        paths, base = dict(opts.get("paths") or {}), opts.get("baseUrl") or ""
        ext = data.get("extends")
        if isinstance(ext, str) and ext.startswith("."):
            parent = (cfg.parent / ext).with_suffix(".json") if not ext.endswith(".json") else cfg.parent / ext
            if parent.exists():
                try:
                    popts = _load_jsonc(parent).get("compilerOptions") or {}
                    paths = {**(popts.get("paths") or {}), **paths}
                    base = base or popts.get("baseUrl") or ""
                except (OSError, json.JSONDecodeError):
                    pass
        self.configs[dir_rel] = TsConfig(dir_rel, base, paths)

    def _config_for(self, rel_path: str) -> TsConfig | None:
        d = str(PurePosixPath(rel_path).parent)
        while True:
            key = "" if d in (".", "") else d
            if key in self.configs:
                return self.configs[key]
            if key == "":
                return None
            d = str(PurePosixPath(d).parent)

    def parse(self, rel_path: str, source: bytes):
        grammar = "tsx" if rel_path.endswith((".tsx", ".jsx")) else "typescript" if rel_path.endswith((".ts", ".mts", ".cts")) else "javascript"
        return parser_for(grammar).parse(source)

    def _provides(self, rel_path: str) -> list[str]:
        out = [rel_path, _strip_ext(rel_path)]
        if PurePosixPath(rel_path).name in _INDEX:
            out.append(str(PurePosixPath(rel_path).parent))
        return out

    def _resolve(self, spec: str, rel_path: str) -> str:
        if spec.startswith("."):
            return _strip_ext(_norm((PurePosixPath(rel_path).parent / spec).parts))
        if spec.startswith("/"):
            return _strip_ext(spec.lstrip("/"))
        cfg = self._config_for(rel_path)
        if cfg is not None:
            hit = cfg.resolve(spec)
            if hit is not None:
                return _strip_ext(hit)
        return spec  # bare specifier: a package

    def extract(self, rel_path: str, source: bytes) -> SourceFile:
        tree = self.parse(rel_path, source)
        sf = SourceFile(path=rel_path, language=self.language)
        sf.provides = self._provides(rel_path)
        for node in walk(tree.root_node, {"import_statement", "export_statement"}):
            src = node.child_by_field_name("source")
            if src is None:
                continue
            spec = "".join(text(c) for c in walk(src, {"string_fragment"}))
            if spec:
                sf.imports.append(Import(self._resolve(spec, rel_path), node.start_point[0] + 1, text(node).strip()[:120]))
        for call in walk(tree.root_node, {"call_expression"}):
            fn = call.child_by_field_name("function")
            args = call.child_by_field_name("arguments")
            if fn is None or args is None or text(fn) not in ("require", "import"):
                continue
            first = next((c for c in args.children if c.type in ("string", "template_string")), None)
            if first is None:
                continue
            spec = "".join(text(c) for c in walk(first, {"string_fragment"}))
            if spec:
                sf.imports.append(Import(self._resolve(spec, rel_path), call.start_point[0] + 1, text(call).strip()[:120]))
        sf.imports.sort(key=lambda i: i.line)
        return sf
