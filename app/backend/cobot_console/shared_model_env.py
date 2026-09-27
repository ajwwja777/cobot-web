"""Reuse one RLT model across collection and evaluation between episodes.

The upstream actor, executor, HIL state machine and learning algorithm stay intact.
Only the recorder/replay boundary depends on the explicitly selected session use.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

from .evaluation_env import EvaluationEpisodeLifecycle
from .paths import RUNTIME_ROOT

BETWEEN_EPISODES = frozenset({"disarmed", "armed", "ready", "waiting_scene", "stopped"})


def session_use(path=None):
    path = Path(path or os.environ.get(
        "COBOT_MODEL_SESSION_USE", str(RUNTIME_ROOT / "deployment/session-use.json")))
    try:
        value = json.loads(path.read_text())["use"]
    except (OSError, ValueError, KeyError, TypeError):
        value = "evaluation"
    if value not in {"evaluation", "collection"}:
        raise ValueError("Unknown model session use")
    return value


class SharedEpisodeLifecycle:
    """Latch purpose and storage once, before recording or robot movement."""

    def __init__(self, recorder, *, read_use=session_use, root_for_phase=None, phase="online"):
        self.recorder = recorder
        self.evaluation = EvaluationEpisodeLifecycle()
        self.read_use = read_use
        self.root_for_phase = root_for_phase
        self.phase = phase
        self.episode_use = None
        self.identity = None

    @property
    def collecting(self):
        return self.episode_use == "collection"

    @property
    def client(self):
        return self.recorder if self.collecting else self.evaluation

    def start_episode(self, identity):
        self.episode_use = self.read_use()
        if self.collecting and self.root_for_phase:
            root = self.root_for_phase(self.phase)
            root.mkdir(parents=True, exist_ok=True)
            identity = replace(identity, data_root=str(root))
        self.identity = identity
        return self.client.start_episode(identity)

    def finish_episode(self, reference, outcome):
        # Keep the latched purpose until the next start: replay is committed
        # asynchronously after this call returns.
        return self.client.finish_episode(reference, outcome)

    def status(self):
        return self.client.status()

    def set_capture_enabled(self, enabled):
        return self.client.set_capture_enabled(enabled)

    def record_marker(self, kind):
        return self.client.record_marker(kind)


class CollectionTrace:
    def __init__(self, writer, lifecycle):
        self.writer, self.lifecycle = writer, lifecycle

    def start_episode(self):
        if self.lifecycle.collecting:
            self.writer.start_episode()

    def append(self, record):
        if self.lifecycle.collecting:
            self.writer.append(record)

    def discard(self):
        if self.lifecycle.collecting:
            self.writer.discard()


def create_shared_env():
    from methods.openpi_rlt.cobot_adapter import task5_client
    from methods.openpi_rlt.plug_v3_yyshadow.right_arm_env import create_right_arm_online_env
    from .profile_storage import selected_root

    original = task5_client.Task5Client
    phase = os.environ.get("COBOT_RLT_COLLECTION_PHASE", "online")

    def lifecycle(*args, **kwargs):
        return SharedEpisodeLifecycle(original(*args, **kwargs),
                                      root_for_phase=selected_root, phase=phase)

    task5_client.Task5Client = lifecycle
    try:
        env = create_right_arm_online_env()
    finally:
        task5_client.Task5Client = original

    application = env._io._session_application
    recorder = application._task5
    commit = env.replay_commit_allowed
    env.replay_commit_allowed = lambda: recorder.collecting and commit()
    env._io._trace_writer = CollectionTrace(env._io._trace_writer, recorder)
    takeover = application.update_takeover
    intervention = {"active": False, "count": 0}

    def update_takeover(*, left, right):
        active = bool(left or right)
        if active and not intervention["active"]:
            intervention["count"] += 1
        intervention["active"] = active
        return takeover(left=left, right=right)

    application.update_takeover = update_takeover
    original_status = application.status

    def status():
        result = original_status()
        use = session_use() if result["phase"] in BETWEEN_EPISODES else recorder.episode_use
        return {
            **result, "shared_model": True, "evaluation_only": use == "evaluation",
            "session_use": use, "data_phase": phase,
            "replay_eligible": recorder.collecting and result.get("replay_eligible", False),
            "intervention_count": intervention["count"],
            "deployment_model_id": os.environ["COBOT_DEPLOYMENT_MODEL_ID"],
            "recording_data_root": recorder.identity.data_root if recorder.identity and recorder.collecting else None,
        }

    application.status = status
    return env


def prepare_config(mode, snapshot, target):
    """Frozen/reference policies keep their weights; collection uses existing replay."""
    import yaml
    from .evaluation_env import prepare_config as prepare_evaluation_config
    from .paths import RLT
    source = RLT / "methods/openpi_rlt/plug_v3_yyshadow/online_rl_frozen.yaml"
    original = yaml.safe_load(source.read_text())
    prepare_evaluation_config(mode, snapshot, target)
    config = yaml.safe_load(target.read_text())
    journal = Path(original["runtime"]["replay"]["journal_path"])
    if not journal.is_absolute():
        journal = (source.parent / journal).resolve()
    config["runtime"]["replay"]["journal_path"] = str(journal)
    target.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("reference", "frozen"))
    parser.add_argument("snapshot")
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare_config(args.mode, args.snapshot, args.target)
