#!/usr/bin/env bash
COBOT_PLATFORM_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export COBOT_PLATFORM_ROOT
eval "$(PYTHONPATH="$COBOT_PLATFORM_ROOT/app/backend" python3 - <<'PYENV'
import os, shlex
from cobot_console.paths import configure_environment, SETTINGS
values = configure_environment()
values["TASK5_ROS_SETUP"] = SETTINGS.get("ros_setup", "/opt/ros/noetic/setup.bash")
for key in values:
    print("export " + key + "=" + shlex.quote(os.environ.get(key, values[key])))
PYENV
)"
export PYTHONPATH="$COBOT_PLATFORM_ROOT/scripts/ros_log_compat:$COBOT_PLATFORM_ROOT/app/backend${PYTHONPATH:+:$PYTHONPATH}"
