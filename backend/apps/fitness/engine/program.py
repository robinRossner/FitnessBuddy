"""Compatibility shim, remove in wave 3: moved to `apps.fitness.engine.core.program`.

The old path is an alias of the new module (the same object in `sys.modules`), so
imports and monkeypatching through it reach the code that runs."""

import sys

from apps.fitness.engine.core import program as _module

sys.modules[__name__] = _module
