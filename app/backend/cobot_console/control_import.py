"""Resolve the installed control package or the configured sibling checkout."""
import sys
from .paths import CONTROL
def control_package():
    source = str(CONTROL / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
