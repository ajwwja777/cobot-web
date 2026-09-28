"""Locate the lightweight sibling RLT integration without importing algorithms."""
import sys
from .paths import RLT
def runtime_package():
    value = str(RLT)
    if value not in sys.path:
        sys.path.insert(0, value)
