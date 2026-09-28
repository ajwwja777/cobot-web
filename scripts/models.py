#!/usr/bin/env python3
"""Model task control using the same runtime as HTTP; load never starts an Episode."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/backend"))
from cobot_console.paths import configure_environment
configure_environment()
from cobot_console.deployment import catalog, ManagedRuntime
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list","check","load","status","logs","pause","resume","start","stop","unload"])
    parser.add_argument("model", nargs="?")
    args = parser.parse_args()
    runtime = ManagedRuntime()
    if args.action in {"list","check","load"}:
        models = catalog()
        if args.action == "list": result = models
        else:
            model = next((m for m in models if m["id"] == args.model), None)
            if not model: parser.error("Unknown model id; run list")
            if args.action == "check": result = model
            else:
                if not model["available"]: parser.error(model["unavailable_reason"])
                result = runtime.load(model)
    elif args.action in {"unload","stop"}: result = runtime.unload()
    elif args.action == "logs":
        print(runtime.status().get("log_tail","")); return
    elif args.action == "status": result = runtime.status()
    else: result = runtime.action(args.action)
    print(json.dumps(result, indent=2, ensure_ascii=False))
if __name__ == "__main__":
    main()
