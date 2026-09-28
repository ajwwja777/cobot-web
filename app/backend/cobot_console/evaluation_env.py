"""Compatibility import; implementation is owned by rl-platform."""
import importlib
import sys
from .rl_import import runtime_package
runtime_package()
_module = importlib.import_module("integrations.cobot_runtime.evaluation_env")
if __name__ == "__main__":
    import runpy
    runpy.run_module(_module.__name__, run_name="__main__")
else:
    sys.modules[__name__] = _module
