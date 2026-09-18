"""archfence: polyglot architecture linter.

Importing the package is the composition root: it loads the checks, which register their config
parsers with core. Core itself never imports a check.
"""
__version__ = "0.2.0"

from . import checks as _checks  # noqa: E402,F401  (registration side effect)
