"""Machine paths; importing the web app does not require robot/model files."""
import json
import os
from pathlib import Path
PROJECT = Path(__file__).resolve().parents[3]
CONFIG = Path(os.environ.get("COBOT_HOST_CONFIG", str(PROJECT / "configs/local.json")))
SETTINGS = json.loads(CONFIG.read_text()) if CONFIG.is_file() else {}
def path(key, default):
    return Path(os.environ.get("COBOT_" + key.upper(), SETTINGS.get(key, str(default)))).expanduser()
RUNTIME_ROOT = path("runtime_root", PROJECT / "runtime")
RLT = path("rlt_project_root", PROJECT.parent / "rl-platform/methods/rlt")
DATA = path("data_root", PROJECT / "data")
LEGACY_DATA = path("legacy_data_root", DATA)
PI05 = path("pi05_root", PROJECT.parent / "vla-platform/models/pi05")
PI05_DAGGER = path("pi05_dagger_root", PROJECT.parent / "vla-platform/models/pi05-dagger")
def configure_environment():
    defaults = {
        "COBOT_PLATFORM_ROOT": str(PROJECT),
        "COBOT_RUNTIME_ROOT": str(RUNTIME_ROOT),
        "COBOT_RLT_PROJECT_ROOT": str(RLT),
        "COBOT_RLT_RUN_ROOT": str(RLT / "runs/plug_v3_yyshadow"),
        "COBOT_LEGACY_DATA_ROOT": str(LEGACY_DATA),
        "COBOT_ADDITIONAL_DATA_ROOTS": json.dumps([str(LEGACY_DATA)] if LEGACY_DATA != DATA else []),
        "COBOT_DATA_UI_RUNTIME_DIR": str(RUNTIME_ROOT / "data-console"),
        "COBOT_CONSOLE_JOB_RUNTIME": str(RUNTIME_ROOT / "console-jobs"),
        "TASK5_SEGMENTED_DATA_ROOT": str(DATA / "raw"),
        "TASK5_SEGMENTED_ALLOWED_DATA_ROOT": str(DATA),
        "COBOT_RLT_TASK5_DATA_ROOT": str(LEGACY_DATA / "rlt/plug_v3_yyshadow/warmup"),
        "COBOT_RLT_LIFECYCLE_STATE": str(RLT / "runs/plug_v3_yyshadow/backend/state.json"),
        "COBOT_RLT_BACKEND_URL": SETTINGS.get("backend_url", "http://127.0.0.1:8026"),
        "COBOT_RLT_MODEL_MANIFEST": str(RLT / "deployments/plug_v3_yyshadow/manifest.json"),
        "COBOT_DATA_PROFILE": "plug_v3_yyshadow",
        "COBOT_PI05_ROOT": str(PI05),
        "COBOT_PI05_DAGGER_ROOT": str(PI05_DAGGER),
        "COBOT_RECORDING_LAYOUT": "flat",
        "COBOT_HOST_DISK_PATH": SETTINGS.get("host_disk_path", "/"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return defaults
