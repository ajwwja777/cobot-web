#!/usr/bin/env python3
"""Export local metadata to static JSON without loading or contacting any service."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/backend"))
from cobot_console.rollout_review import catalog_runs, read_json, read_run


def export(args):
    if args.manifest:
        specs = read_json(args.manifest).get("runs", [])
    else:
        specs = catalog_runs(read_json(args.catalog), args.data_root)
    runs, errors = [], []
    for spec in specs:
        try:
            runs.append(read_run(spec, args.cache_root, args.limit))
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(dict(model_id=spec.get("model_id"), error=type(error).__name__))
    value = dict(schema=1, sampled_at=time.time(), runs=runs, errors=errors)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".%d.tmp" % os.getpid())
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    os.replace(temporary, target)
    return dict(runs=len(runs), episodes=sum(len(run["episodes"]) for run in runs), errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="JSON {runs:[{model_id,options,run_dir,data_root}]}")
    source.add_argument("--catalog", help="Registered deployment_models.json; local files only")
    parser.add_argument("--data-root", help="Expand catalogue {DATA}, if needed")
    parser.add_argument("--cache-root", help="Existing Web runtime/rl-monitoring cache, read-only")
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
