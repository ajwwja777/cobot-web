#!/usr/bin/env python3
"""Compatibility entry for the control-owned foreground supervisor."""
import os
import sys
from pathlib import Path
root = Path(os.environ.get("COBOT_CONTROL_PROJECT_ROOT", Path(__file__).resolve().parents[2] / "cobot-control"))
os.execv(sys.executable, [sys.executable, str(root / "scripts/site_device.py"), *sys.argv[1:]])
