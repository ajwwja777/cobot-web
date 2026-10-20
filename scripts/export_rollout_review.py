#!/usr/bin/env python3
"""Export local metadata to static JSON without loading or contacting any service."""
import argparse
import ast
from collections import deque
import fcntl
import json
import os
from pathlib import Path
import sys
import time
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/backend"))
from cobot_console.rollout_review import catalog_runs, read_json, read_run


def original_success_series(path):
    """Compile only the existing pure statistic function, not the service module."""
    if not path:
        return None
    try:
        tree = ast.parse(Path(path).read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "success_series")
        scope = {"deque": deque}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
        return scope["success_series"]
    except (OSError, ValueError, SyntaxError, StopIteration):
        return None


def write_if_changed(path, value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    try:
        if path.read_text() == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".%d.tmp" % os.getpid())
    temporary.write_text(text)
    os.replace(temporary, path)
    return True


def export(args):
    if args.manifest:
        specs = read_json(args.manifest).get("runs", [])
    else:
        specs = catalog_runs(read_json(args.catalog), args.data_root)
    target = Path(args.output)
    previous = read_json(target)
    prior_runs = {(run.get("model_id"), run.get("run_key")): run for run in previous.get("runs", [])}
    module = getattr(args, "success_module", None)
    if not module and args.catalog:
        module = Path(args.catalog).resolve().parents[1] / "methods/expo_ft/cobot_adapter/monitoring.py"
    success_series = original_success_series(module)
    runs, errors, changed = [], [], 0
    for spec in specs:
        try:
            run = read_run(spec, args.cache_root, args.limit, include_rollouts=True,
                           success_series=success_series, strict_archive=True)
            for entry in run["episodes"]:
                payload = entry.pop("_rollout", None)
                if payload is not None:
                    path = target.parent / "rollout-review" / run["run_key"] / (entry["episode_uuid"] + ".json")
                    changed += write_if_changed(path, payload)
            runs.append(run)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            errors.append(dict(model_id=spec.get("model_id"), error=type(error).__name__))
            # A transient archive/read failure must not erase playable history.
            import hashlib
            key = hashlib.sha256(str(Path(spec["run_dir"])).encode()).hexdigest()[:20]
            saved = prior_runs.get((spec.get("model_id"), key))
            if saved:
                runs.append(saved)
    value = dict(schema=1, runs=runs, errors=errors)
    old_content = {key: value for key, value in previous.items() if key != "sampled_at"}
    value["sampled_at"] = previous.get("sampled_at") if value == old_content else time.time()
    changed += write_if_changed(target, value)
    return dict(runs=len(runs), episodes=sum(len(run["episodes"]) for run in runs), errors=errors, changed=changed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="JSON {runs:[{model_id,options,run_dir,data_root}]}")
    source.add_argument("--catalog", help="Registered deployment_models.json; local files only")
    parser.add_argument("--data-root", help="Expand catalogue {DATA}, if needed")
    parser.add_argument("--cache-root", help="Existing Web runtime/rl-monitoring cache, read-only")
    parser.add_argument("--success-module", help="Existing RL monitoring.py containing pure success_series; auto from --catalog")
    parser.add_argument("--output", required=True)
    parser.add_argument("--interval", type=float, default=0, help="0: one snapshot; otherwise seconds, minimum 5")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()
    if args.interval and args.interval < 5:
        parser.error("--interval must be 0 or at least 5 seconds")
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Only our own output lock; never any robot/model lifecycle lock.
    with target.with_name(target.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            print(json.dumps(export(args)), flush=True)
            if not args.interval:
                return
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
