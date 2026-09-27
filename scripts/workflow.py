#!/usr/bin/env python3
"""Start/stop only the web service; hardware and models are separate tasks."""
import argparse, json, os, sys
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app/backend"))
from cobot_console.paths import configure_environment, SETTINGS, RUNTIME_ROOT, RLT, DATA
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=["status", "ui-up", "ui-down", "ui-status"])
    ap.add_argument("--profile", default="plug_v3_yyshadow")
    args = ap.parse_args()
    configure_environment()
    os.environ.setdefault("COBOT_DATA_PROFILE", args.profile)
    os.environ.setdefault("TASK5_PYTHON", str(PROJECT / ".venv/bin/python"))
    os.environ.setdefault("TASK5_ROS_SETUP", SETTINGS.get("ros_setup", ""))
    os.environ.setdefault("COBOT_RLT_SCRIPTS_DIR", str(RLT / "deployments/openpi-rlt/plug-insertion-stage1-v2/runtime-overlay/methods/openpi_rlt/scripts"))
    if args.action == "status":
        print(json.dumps({"project":str(PROJECT),"runtime":str(RUNTIME_ROOT),"data":str(DATA),
                          "rlt":str(RLT),"rlt_available":RLT.is_dir()}, indent=2))
        return
    names = {"ui-up":"start_cobot_data_ui.sh","ui-down":"stop_cobot_data_ui.sh","ui-status":"check_cobot_data_ui.sh"}
    os.execvp("bash", ["bash", str(PROJECT / "app/backend/scripts" / names[args.action])])
if __name__ == "__main__":
    main()
