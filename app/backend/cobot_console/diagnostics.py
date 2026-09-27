"""Bounded read-only telemetry for the unified Cobot console."""
from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np

DEFAULT_RUN_ROOT = Path(os.environ.get(
    "COBOT_RLT_RUN_ROOT",
    str(Path(__file__).resolve().parents[4] / "rl-platform/outputs/rlt/plug_v3_yyshadow"),
))
_METRIC_FIELDS = (
    "global_step", "actor_version", "actor_loss", "critic_loss",
    "q1_mean", "q2_mean", "target_q_mean", "actor_q",
    "bc_human_penalty", "bc_ref_penalty", "human_mask_ratio",
    "policy_mask_ratio", "did_actor_update", "replay_size",
    "bc_penalty", "delta_penalty", "weighted_bc", "weighted_q",
    "weighted_delta", "bc_weight", "q_weight", "delta_weight", "td_error",
    "pending_update_budget", "sample_recent_online_ratio",
    "sample_warmup_demo_ratio", "sample_human_intervention_ratio",
    "sample_success_ratio", "sample_source_base_ratio",
    "sample_source_rl_ratio", "sample_source_human_ratio",
)
_JOINT_STREAMS = ("front_left", "front_right", "policy_left", "policy_right")
_TELEMETRY_TAIL_BYTES = 512 * 1024


class ConsoleDiagnostics:
    """Aggregate stable diagnostic files without touching control state."""

    def __init__(
        self,
        run_root: Path = DEFAULT_RUN_ROOT,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_metrics: int = 240,
    ) -> None:
        self.root = Path(run_root).expanduser().resolve()
        self.monotonic = monotonic
        self.wall_clock = wall_clock
        self.max_metrics = int(max_metrics)

    def _inside(self, path: Path, *, must_exist: bool = True) -> Path:
        resolved = path.expanduser().resolve(strict=must_exist)
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ValueError("release_outside_root")
        return resolved

    @staticmethod
    def _json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _tail_lines(path: Path, limit: int):
        """Complete lines from the last `limit` bytes; append-only logs stay cheap to poll."""
        with path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - limit))
            if stream.tell():
                stream.readline()
            yield from stream

    def _metrics(self, path: Path) -> list:
        rows = deque(maxlen=self.max_metrics)
        try:
            for raw in self._tail_lines(path, 2 * 1024 * 1024):
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(value, dict):
                    continue
                row = {}
                valid = True
                for key in _METRIC_FIELDS:
                    if key not in value:
                        continue
                    number = value[key]
                    if isinstance(number, bool) or not isinstance(number, (int, float)):
                        continue
                    number = float(number)
                    if not math.isfinite(number):
                        valid = False
                        break
                    row[key] = number
                if valid and row:
                    rows.append(row)
        except OSError:
            pass
        return list(rows)

    def _release(self, availability: Dict[str, str]):
        current = self._json(self.root / "learning/rtc-v5/current.json")
        if not current or not isinstance(current.get("release"), str):
            availability["release"] = "unavailable"
            return None, [], {"release_count": 0}, None
        releases = []
        seen = set()
        try:
            path = self._inside(Path(current["release"]))
            while len(releases) < 64 and path not in seen:
                seen.add(path)
                value = self._json(path)
                if value is None:
                    break
                releases.append((value, path))
                # A rollback restores an older actor: its training history is the restored
                # release's lineage, not the chain it replaced.
                parent = value.get("rollback_of") or value.get("parent_release")
                if not isinstance(parent, str) or not parent:
                    break
                path = self._inside(Path(parent))
        except (OSError, ValueError) as error:
            availability["release"] = str(error) or "unavailable"
            return None, [], {"release_count": 0}, None
        if not releases:
            availability["release"] = "invalid_release"
            return None, [], {"release_count": 0}, None
        rows_by_step = {}
        metric_files = 0
        boundaries = []
        for value, _path in reversed(releases):
            step = value.get("global_step")
            if isinstance(step, (int, float)) and not isinstance(step, bool):
                boundaries.append({"name": str(value.get("name", "")), "global_step": float(step)})
            audit_path = value.get("offline_audit")
            if not isinstance(audit_path, str):
                continue
            try:
                folder = self._inside(Path(audit_path)).parent
                rows = self._metrics(folder / "metrics.jsonl")
            except (OSError, ValueError):
                continue
            if rows:
                metric_files += 1
            release_step=value.get("global_step")
            release_step=float(release_step) if isinstance(release_step,(int,float)) else None
            for row in rows:
                step = row.get("global_step")
                if step is not None and (release_step is None or float(step)<=release_step):
                    rows_by_step[float(step)] = row
        metrics = [rows_by_step[key] for key in sorted(rows_by_step)][-self.max_metrics:]
        availability["release"] = "ok"
        availability["metrics"] = "ok" if metrics else "unavailable"
        value = releases[0][0]
        release = {
            "name": value.get("name"), "global_step": value.get("global_step"),
            "actor_version": value.get("actor_updates", value.get("global_step")),
            "actor_updates": value.get("actor_updates"), "status": value.get("status"),
            "parent_release": value.get("parent_release"),
            "online_update": value.get("online_update") or {},
            "execution": value.get("execution") or {},
            "training_protocol": value.get("training_protocol") or {},
            "onsite_validated": bool(value.get("onsite_validated", False)),
            "online_improvement_validated": bool(value.get("online_improvement_validated", False)),
        }
        oldest = metrics[0].get("global_step") if metrics else None
        history = {"release_count": len(releases), "metric_file_count": metric_files,
                   "oldest_step": oldest,
                   "latest_step": metrics[-1].get("global_step") if metrics else None,
                   # Only releases inside the plotted range; same-step reissues collapse to the newest name.
                   "releases": list({b["global_step"]: b for b in boundaries
                                     if oldest is not None and b["global_step"] >= oldest}.values())[-64:]}
        return release, metrics, history, self._release_episode_q(value)

    def _release_episode_q(self, value):
        """Held-out episode-Q evaluation of the deployed release itself, when its training run wrote one."""
        audit, step = value.get("offline_audit"), value.get("global_step")
        if not isinstance(audit, str) or not isinstance(step, (int, float)) or isinstance(step, bool):
            return None
        try:
            folder = self._inside(Path(audit)).parent
        except (OSError, ValueError):
            return None
        return self._json(folder / f"episode_q_{int(step)}.json")

    def _telemetry_rows(self, path: Path, scalar_fields, array_fields=()):
        rows = deque(maxlen=self.max_metrics)
        try:
            for raw in self._tail_lines(path, _TELEMETRY_TAIL_BYTES):
                try: value = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError): continue
                if not isinstance(value, dict): continue
                row = {}
                for key in scalar_fields:
                    number = value.get(key)
                    if isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(float(number)):
                        row[key] = float(number)
                for key in array_fields:
                    values = value.get(key)
                    if isinstance(values, list) and len(values) <= 32 and all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(float(x)) for x in values):
                        row[key] = [float(x) for x in values]
                if row: rows.append(row)
        except OSError:
            pass
        return list(rows)

    def _telemetry(self):
        folder = self.root / "learning/telemetry"
        episode_q = self._json(folder / "episode_q.json")
        decision_q = self._telemetry_rows(folder / "decision_q.jsonl",
            ("decision","actor_q","reference_q","executed_q","td_error","actor_version","learner_step","latency_ms"))
        actor_delta = self._telemetry_rows(folder / "actor_delta.jsonl",
            ("decision","actor_version","learner_step","overall_rms"), ("horizon_rms","joint_rms","raw_rms","projected_rms","conditioned_rms"))
        raw_progress = self._json(folder / "progress.json") or {}
        progress = None
        completed, total = raw_progress.get("completed"), raw_progress.get("total")
        if isinstance(completed,(int,float)) and isinstance(total,(int,float)) and total > 0:
            progress = {"phase":str(raw_progress.get("phase","unknown")),
                        "completed":int(completed),"total":int(total),
                        "fraction":max(0.0,min(1.0,float(completed)/float(total))),
                        "message":str(raw_progress.get("message", ""))}
        return episode_q, decision_q, actor_delta, progress

    @staticmethod
    def _explanation(cycle, operation):
        phase = str((operation or {}).get("phase", ""))
        if phase in ("preparing", "training"):
            return {
                "code": "update_" + phase,
                "severity": "active",
                "transitions": int((operation or {}).get("new_transitions", 0) or 0),
                "updates": int((operation or {}).get("updates", 0) or 0),
            }
        cycle_phase = str((cycle or {}).get("phase", "unavailable"))
        if cycle_phase == "waiting":
            pending = int((cycle or {}).get("pending_episodes", 0) or 0)
            required = int((cycle or {}).get("episodes_per_update", 5) or 5)
            return {
                "code": "waiting_for_episodes",
                "severity": "info",
                "pending": pending,
                "required": required,
                "missing": max(0, required - pending),
                "quarantined": int(
                    (cycle or {}).get("quarantined_episodes", 0) or 0
                ),
            }
        if cycle_phase in ("accepted", "rejected"):
            return {
                "code": "candidate_" + cycle_phase,
                "severity": "success" if cycle_phase == "accepted" else "warning",
                "episodes": len((cycle or {}).get("new_uuids", []) or []),
            }
        return {"code": "update_state_unavailable", "severity": "muted"}


    @staticmethod
    def _cycle_view(value):
        if not isinstance(value, dict):
            return None
        keys = (
            "phase", "pending_episodes", "new_since_attempt",
            "quarantined_episodes", "episodes_per_update",
        )
        result = {key: value.get(key) for key in keys if key in value}
        if isinstance(value.get("new_uuids"), list):
            result["new_uuids"] = value["new_uuids"][:5]
        return result

    @staticmethod
    def _operation_view(value):
        if not isinstance(value, dict):
            return None
        keys = (
            "phase", "actor_version", "learner_final_step", "accepted",
            "updates", "new_transitions", "error",
        )
        result = {key: value.get(key) for key in keys if key in value}
        if isinstance(value.get("new_uuids"), list):
            result["new_uuids"] = value["new_uuids"][:5]
        if isinstance(value.get("reasons"), list):
            result["reasons"] = [str(item) for item in value["reasons"][:20]]
        return result

    @staticmethod
    def _session_view(value):
        if not isinstance(value, dict):
            return None
        keys = (
            "phase", "policy_paused", "chunk_count",
            "last_inference_latency_sec", "actor_version", "learner_version",
            "fault_reason", "recorded_episode_count", "episode_display_number",
            "data_root", "data_phase", "rtc_deadline_recoveries",
            "model_rpc_reconnects",
        )
        result = {key: value.get(key) for key in keys if key in value}
        profile = value.get("runtime_profile")
        if isinstance(profile, dict):
            result["runtime_profile"] = {
                key: profile.get(key)
                for key in (
                    "rtc_mode", "control_hz", "velocity_limit",
                    "acceleration_limit", "tracking_bound",
                )
                if key in profile
            }
        return result

    def _robot(self, cache) -> Dict[str, Any]:
        snapshot = cache.snapshot(float(self.monotonic()))
        result: Dict[str, Any] = {}
        velocities = []
        for key in _JOINT_STREAMS:
            value = snapshot.get(key)
            if not isinstance(value, dict):
                continue
            stream: Dict[str, Any] = {
                "fresh": bool(snapshot.is_fresh(key)),
                "age_sec": max(
                    0.0, float(snapshot.now - snapshot.arrival_timestamp(key))
                ),
            }
            for field in ("position", "velocity"):
                array = np.asarray(value.get(field, ()), dtype=np.float64).reshape(-1)
                if array.shape == (7,) and np.isfinite(array).all():
                    stream[field] = [float(item) for item in array]
                    if field == "velocity" and key.startswith("front_"):
                        velocities.extend(abs(float(item)) for item in array[:6])
            result[key] = stream
        result["summary"] = {
            "max_abs_velocity": max(velocities) if velocities else None,
            "fresh_joint_streams": sum(
                bool(result.get(key, {}).get("fresh")) for key in _JOINT_STREAMS
            ),
        }
        return result

    def _snapshot_v3(self, cache, session_loader) -> Dict[str, Any]:
        availability: Dict[str, str] = {}
        metrics_path = self.root / "online/metrics/learner_metrics.jsonl"
        status = self._json(self.root / "online/metrics/learner_status.json") or {}
        metrics = self._metrics(metrics_path)
        availability["metrics"] = "ok" if metrics_path.is_file() else "awaiting_warmup"
        availability["learner"] = "ok" if status else "awaiting_warmup"
        step = int(status.get("global_step", 0) or 0)
        actor_version = status.get("actor_version", status.get("published_actor_version"))
        replay_size = int(status.get("replay_size", 0) or 0)
        warmup_required = int(status.get("warmup_min_size", 600) or 600)
        ready = bool(status.get("ready_for_online", False))
        if ready:
            phase, message = "ready", "Warmup complete; online actor is available"
            fraction = 1.0
        elif replay_size < warmup_required:
            phase, message = "collecting", "Collecting warmup replay"
            fraction = replay_size / max(1, warmup_required)
        else:
            phase, message = "training", "Training warmup actor and critic"
            fraction = step / 20000.0
        operation = {
            "phase": phase, "accepted": ready, "actor_version": actor_version,
            "updates": step, "new_transitions": replay_size, "reasons": [],
        }
        cycle = {
            "phase": phase, "pending_episodes": replay_size,
            "episodes_per_update": warmup_required, "quarantined_episodes": 0,
        }
        release = {
            "name": "plug_v3-online" if ready else "plug_v3-stage1-reference",
            "actor_version": actor_version, "actor_updates": step,
            "global_step": step, "status": "ready" if ready else "warmup",
            "onsite_validated": False, "online_improvement_validated": False,
        }
        try:
            raw_session = session_loader()
            session = self._session_view(raw_session)
            availability["session"] = "ok" if raw_session is not None else "offline"
        except Exception:
            session = None
            availability["session"] = "unavailable"
        progress = {
            "fraction": max(0.0, min(1.0, float(fraction))),
            "completed": replay_size if replay_size < warmup_required else step,
            "total": warmup_required if replay_size < warmup_required else 20000,
            "message": message,
        }
        return {
            "generated_at": float(self.wall_clock()), "availability": availability,
            "learning": {
                "cycle": cycle, "operation": operation, "release": release,
                "metrics": metrics, "history": {"release_count": 1, "releases": []},
                "episode_q": None, "decision_q": [], "actor_delta": [],
                "progress": progress,
                "update_explanation": {
                    "code": "update_training" if phase == "training" else "waiting_for_episodes",
                    "pending": replay_size, "required": warmup_required,
                    "missing": max(0, warmup_required - replay_size),
                    "quarantined": 0, "transitions": replay_size, "updates": step,
                },
            },
            "robot": self._robot(cache), "session": session,
        }

    def snapshot(self, cache, session_loader) -> Dict[str, Any]:
        if self.root.name == "plug_v3_yyshadow":
            return self._snapshot_v3(cache, session_loader)
        availability: Dict[str, str] = {}
        raw_cycle = self._json(self.root / "learning/rtc-v5/online_cycle.json")
        raw_operation = self._json(self.root / "learning/operation.json")
        availability["cycle"] = "ok" if raw_cycle is not None else "unavailable"
        availability["operation"] = "ok" if raw_operation is not None else "unavailable"
        cycle = self._cycle_view(raw_cycle)
        operation = self._operation_view(raw_operation)
        release, metrics, history, release_episode_q = self._release(availability)
        telemetry_episode_q, decision_q, actor_delta, progress = self._telemetry()
        # The telemetry file holds whatever candidate the online cycle evaluated last, accepted or not;
        # the deployed release's own evaluation is the one that describes the running critic.
        if release_episode_q is not None:
            episode_q = dict(release_episode_q, source="release", source_step=(release or {}).get("global_step"))
        elif telemetry_episode_q is not None:
            episode_q = dict(telemetry_episode_q, source="latest_candidate")
        else:
            episode_q = None
        try:
            raw_session = session_loader()
            session = self._session_view(raw_session)
            availability["session"] = "ok" if raw_session is not None else "offline"
        except Exception:
            session = None
            availability["session"] = "unavailable"
        return {
            "generated_at": float(self.wall_clock()),
            "availability": availability,
            "learning": {
                "cycle": cycle,
                "operation": operation,
                "release": release,
                "metrics": metrics,
                "history": history,
                "episode_q": episode_q,
                "decision_q": decision_q,
                "actor_delta": actor_delta,
                "progress": progress,
                "update_explanation": self._explanation(cycle, operation),
            },
            "robot": self._robot(cache),
            "session": session,
        }
