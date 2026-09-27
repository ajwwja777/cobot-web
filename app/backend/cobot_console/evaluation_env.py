"""Evaluation-only boundary around the existing RLT control runtime.

Same observations, actor and robot executor; no recorder, learner or replay writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


class EvaluationEpisodeLifecycle:
    def __init__(self, *args, **kwargs):
        self.index = 0
        self.active = False
        self.enabled = False

    def status(self):
        return {"state": "recording" if self.active else "idle", "capture_enabled": self.enabled}

    def start_episode(self, identity):
        from methods.openpi_rlt.cobot_adapter.task5_client import Task5EpisodeRef
        self.index += 1
        self.active = self.enabled = True
        return Task5EpisodeRef(identity, self.index, str(uuid4()))

    def finish_episode(self, reference, outcome):
        self.active = self.enabled = False
        return reference

    def set_capture_enabled(self, enabled):
        self.enabled = bool(enabled)
        return self.status()


class NoTraceWriter:
    def append(self, record):
        pass


def create_evaluation_env():
    from methods.openpi_rlt.cobot_adapter import task5_client
    from methods.openpi_rlt.plug_v3_yyshadow.right_arm_env import create_right_arm_online_env

    original = task5_client.Task5Client
    task5_client.Task5Client = EvaluationEpisodeLifecycle
    try:
        env = create_right_arm_online_env()
    finally:
        task5_client.Task5Client = original
    env.replay_commit_allowed = lambda: False
    env._io._trace_writer = NoTraceWriter()
    application = env._io._session_application
    original_takeover = application.update_takeover
    intervention = {"active": False, "count": 0}
    def update_takeover(*, left, right):
        active = bool(left or right)
        if active and not intervention["active"]:
            intervention["count"] += 1
        intervention["active"] = active
        return original_takeover(left=left, right=right)
    application.update_takeover = update_takeover
    original_status = application.status
    application.status = lambda: {
        **original_status(), "evaluation_only": True, "replay_eligible": False,
        "intervention_count": intervention["count"],
        "deployment_model_id": os.environ["COBOT_DEPLOYMENT_MODEL_ID"],
    }
    return env


def prepare_config(mode, snapshot, target):
    import yaml
    from .paths import RLT
    root = RLT
    run = root / "runs/plug_v3_yyshadow"
    config = yaml.safe_load((root / "methods/openpi_rlt/plug_v3_yyshadow/online_rl_frozen.yaml").read_text())
    runtime = config["runtime"]
    if mode == "reference":
        snapshot = run / "candidates/experts120_5k_20260925/actor_snapshot/actor_snapshot.pkl"
    snapshot = Path(snapshot).resolve()
    if not snapshot.is_file():
        raise ValueError("Evaluation actor snapshot is missing: " + str(snapshot))
    stats = snapshot.parent.parent / "action_norm_stats.json"
    if not stats.is_file():
        raise ValueError("Evaluation normalization stats are missing: " + str(stats))
    config["experiment"]["rl"]["action_norm_stats_path"] = str(stats)
    runtime["actor_service"]["snapshot_path"] = str(snapshot)
    runtime["learner_service"]["actor_snapshot_path"] = str(snapshot)
    runtime["learner_service"]["checkpoint_dir"] = str(target.parent / "unused-checkpoints")
    runtime["replay"]["journal_path"] = str(target.parent / ("eval-replay-" + uuid4().hex + ".pkl"))
    runtime["env_driver"]["actor_deterministic"] = True
    runtime["monitoring"]["enable_wandb"] = False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("reference", "frozen"))
    parser.add_argument("snapshot")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare_config(args.mode, args.snapshot, args.target)
