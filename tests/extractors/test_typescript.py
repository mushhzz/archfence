import textwrap
from pathlib import Path

import pytest

from archfence.extractors.typescript import TypeScriptExtractor
from conftest import write


def targets(sf):
    return [i.target for i in sf.imports]


def test_typescript_imports_and_aliases(tmp_path):
    # JSON with comments, trailing commas, and strings that look like comment delimiters ("@/*", "**/*.ts")
    write(tmp_path, "tsconfig.json", '{\n  // comment\n  /* block */\n  "compilerOptions": { "paths": { "@/*": ["./*"], "ui": ["./components/ui/index.tsx"] }, },\n  "include": ["**/*.ts", "url//x"],\n}\n')
    write(tmp_path, "components/ui/index.tsx", "export const B = 1;\n")
    ex = TypeScriptExtractor(tmp_path)
    ex.prepare(["app/page.tsx", "components/ui/index.tsx"])
    sf = ex.extract(
        "app/page.tsx",
        b"import React from 'react';\nimport { a } from '../lib/x';\nimport type { T } from '@/types/t';\nimport * as ns from './ns.js';\n"
        b"export { y } from './y';\nexport * from '@/lib/z';\nconst m = require('./cjs');\nconst d = await import('./dyn');\nimport { B } from 'ui';\n",
    )
    assert sf.provides == ["app/page.tsx", "app/page"]
    assert targets(sf) == ["react", "lib/x", "types/t", "app/ns", "app/y", "lib/z", "app/cjs", "app/dyn", "components/ui/index"]
    idx = ex.extract("components/ui/index.tsx", b"")
    assert idx.provides == ["components/ui/index.tsx", "components/ui/index", "components/ui"]
