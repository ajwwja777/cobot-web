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
CONTROL = path("control_project_root", PROJECT.parent / "cobot-control")
RLT = path("rlt_project_root", PROJECT.parent / "rl-platform")
DATA = path("data_root", PROJECT / "data")
LEGACY_DATA = path("legacy_data_root", DATA)
PI05 = path("pi05_root", PROJECT.parent / "vla-platform/models/pi05")
PI05_DAGGER = path("pi05_dagger_root", PROJECT.parent / "vla-platform/models/pi05-dagger")
RLT_MODELS = path("rlt_model_root", RLT / "models/rlt/plug_v3_yyshadow")
RLT_WARMUP = path("rlt_warmup_root", RLT_MODELS / "warmup-5000")
PI05_CHECKPOINT = path("pi05_checkpoint", PI05 / "checkpoints/step_2000")
PI05_DAGGER_CHECKPOINT = path("pi05_dagger_checkpoint", PI05_DAGGER / "checkpoints/step_3000")
POSE_CONFIG = path("pose_config", DATA / "motion/poses/home_poses.yaml")
def migrated_data_path(value):
    value = str(value)
    for old, new in sorted(SETTINGS.get("data_root_aliases", {}).items(), key=lambda pair: len(pair[0]), reverse=True):
        if value == old or value.startswith(old + "/"):
            return new + value[len(old):]
    return value
def configure_environment():
    defaults = {
        "COBOT_PLATFORM_ROOT": str(PROJECT),
        "COBOT_RUNTIME_ROOT": str(RUNTIME_ROOT),
        "COBOT_RLT_PROJECT_ROOT": str(RLT),
        "COBOT_CONTROL_PROJECT_ROOT": str(CONTROL),
        "COBOT_RLT_RUN_ROOT": str(RLT / "outputs/rlt/plug_v3_yyshadow"),
        "COBOT_LEGACY_DATA_ROOT": str(LEGACY_DATA),
        "COBOT_DATA_ROOT_ALIASES": json.dumps(SETTINGS.get("data_root_aliases", {})),
        "COBOT_ADDITIONAL_DATA_ROOTS": json.dumps([str(LEGACY_DATA)] if LEGACY_DATA != DATA else []),
        "COBOT_DATA_UI_RUNTIME_DIR": str(RUNTIME_ROOT / "data-console"),
        "COBOT_CONSOLE_JOB_RUNTIME": str(RUNTIME_ROOT / "console-jobs"),
        "TASK5_SEGMENTED_DATA_ROOT": str(path("normal_data_root", DATA / "raw")),
        "TASK5_SEGMENTED_ALLOWED_DATA_ROOT": str(DATA),
        "COBOT_RLT_TASK5_DATA_ROOT": str(SETTINGS.get("rlt_data_roots", {}).get("warmup", DATA / "rlt/plug_v3_yyshadow/warmup")),
        "COBOT_RLT_LIFECYCLE_STATE": str(RLT / "outputs/rlt/plug_v3_yyshadow/backend/state.json"),
        "COBOT_RLT_BACKEND_URL": SETTINGS.get("backend_url", "http://127.0.0.1:8026"),
        "COBOT_RLT_MODEL_MANIFEST": str(RLT / "configs/rlt/plug_v3_yyshadow/manifest.json"),
        "COBOT_DATA_PROFILE": "plug_v3_yyshadow",
        "COBOT_ASSET_MOUNT": SETTINGS.get("asset_mount", ""),
        "COBOT_ASSET_UUID": SETTINGS.get("asset_uuid", ""),
        "COBOT_POSE_CONFIG": str(POSE_CONFIG),
        "COBOT_TELEOP_DATA_ROOT": SETTINGS.get("teleop_data_root", str(DATA / "motion/replays")),
        "COBOT_RLT_MODEL_ROOT": str(RLT_MODELS),
        "COBOT_RLT_WARMUP_ROOT": str(RLT_WARMUP),
        "COBOT_RLT_TRACE_ROOT": SETTINGS.get("rlt_trace_root", str(RLT / "outputs/rlt/plug_v3_yyshadow/online/traces")),
        "COBOT_PI05_CHECKPOINT": str(PI05_CHECKPOINT),
        "COBOT_PI05_DAGGER_CHECKPOINT": str(PI05_DAGGER_CHECKPOINT),
        "COBOT_PI05_ROOT": str(PI05),
        "COBOT_PI05_DAGGER_ROOT": str(PI05_DAGGER),
        "COBOT_RECORDING_LAYOUT": "flat",
        "COBOT_HOST_DISK_PATH": SETTINGS.get("host_disk_path", "/"),
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)
    return defaults
