"""Loads the integration's standalone modules by path, because its folder holds a select.py that would shadow the stdlib module."""

import importlib.util
import sys
from pathlib import Path

COMPONENT_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "trimlight_local"


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, COMPONENT_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


trimlight = _load("trimlight")
effects = _load("effects")
