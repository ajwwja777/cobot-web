"""Select which RLT actor release the next Session loads (for on-robot A/B comparisons).

The console never writes the deployment pointer itself: it runs the RLT project's own
`rtc_round switch` (full release validation, pointer backup, switch log) and then restarts the
Session through the RLT lifecycle CLI, which keeps the model loaded. Switching is only offered
between episodes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .paths import RLT
DEFAULT_PROJECT_ROOT = RLT
SELECTABLE_PREFIXES = ("rtc-rollback-", "rtc-round-", "rtc-awbc-candidate-")
SWITCH_PHASES = ("disarmed", "ready", "stopped", "waiting_scene", "offline")
_NAME = re.compile(r"^[A-Za-z0-9._-]+\.json$")
PYTHON = "/usr/bin/python3"


class ReleaseSelectionError(RuntimeError):
    """Rejected selection; the message is safe to show to the operator."""


def _describe(value: Dict[str, Any]) -> str:
    if value.get("rollback_of"):
        return "回滚基线"
    if value.get("actor_objective") == "awbc_candidate_v1" or value.get("advantage"):
        beta = value.get("awbc_beta")
        if isinstance(beta, (int, float)) and beta >= 1e5:
            return "只模仿（不用 critic）"
        return "AWBC · " + ("V 加权" if value.get("advantage") == "v" else "Q 加权")
    return "在线更新"


class ReleaseSelector:
    def __init__(self, project_root: Path = DEFAULT_PROJECT_ROOT, *,
                 runner: Optional[Callable[[Sequence[str], Path, Dict[str, str]], subprocess.CompletedProcess]] = None) -> None:
        self.root = Path(project_root)
        self.releases = self.root / "runs/plug_v2/learning/rtc-v5/releases"
        self.pointer = self.root / "runs/plug_v2/learning/rtc-v5/current.json"
        self.runner = runner or self._run

    @staticmethod
    def _run(command: Sequence[str], cwd: Path, env: Dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(list(command), cwd=str(cwd), env=env, capture_output=True, text=True, timeout=240)

    def current_name(self) -> Optional[str]:
        try:
            return Path(json.loads(self.pointer.read_text())["release"]).name
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def listing(self) -> Dict[str, Any]:
        current = self.current_name()
        items: List[Dict[str, Any]] = []
        for path in sorted(self.releases.glob("*.json")):
            if not (path.name.startswith(SELECTABLE_PREFIXES) or path.name == current):
                continue
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            items.append({
                "name": path.name, "actor_updates": value.get("actor_updates"), "global_step": value.get("global_step"),
                "kind": _describe(value), "current": path.name == current, "created_at": path.stat().st_mtime,
            })
        items.sort(key=lambda item: (not item["current"], -item["created_at"]))
        return {"current": current, "releases": items[:40], "switch_phases": list(SWITCH_PHASES)}

    def switch(self, name: str, session_phase: Optional[str]) -> Dict[str, Any]:
        if not isinstance(name, str) or not _NAME.match(name) or not name.startswith(SELECTABLE_PREFIXES):
            raise ReleaseSelectionError("release_not_selectable")
        if not (self.releases / name).is_file():
            raise ReleaseSelectionError("release_not_found")
        phase = str(session_phase or "offline")
        if phase not in SWITCH_PHASES:
            raise ReleaseSelectionError("switch_between_episodes_only: session is " + phase)
        if name == self.current_name():
            return {"release": name, "changed": False, "message": "已是当前 release"}
        env = dict(os.environ, PYTHONPATH=str(self.root), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        steps = (("switch", [PYTHON, "-m", "methods.openpi_rlt.plug_v2.rtc_round", "switch", name]),
                 ("restart", [PYTHON, "-m", "methods.openpi_rlt.plug_v2.cli", "up", "--actor", "warmup", "--restart"]))
        log = []
        for label, command in steps:
            result = self.runner(command, self.root, env)
            log.append({"step": label, "returncode": result.returncode, "output": (result.stdout + result.stderr)[-2000:]})
            if result.returncode != 0:
                raise ReleaseSelectionError(label + "_failed: " + (result.stderr or result.stdout).strip()[-400:])
        return {"release": name, "changed": True, "steps": log,
                "message": "已切换到 " + name + "，Session 正在以冻结 actor 重新加载"}
