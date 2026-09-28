#!/usr/bin/env python3
"""Compatibility entry: passive diagnosis now belongs to cobot-control."""
import os
import sys
from pathlib import Path
project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "app/backend"))
from cobot_console.paths import configure_environment, CONTROL
configure_environment()
os.execv(sys.executable, [sys.executable, str(CONTROL / "scripts/control.py"), "diagnose", *sys.argv[1:]])
