from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

def test_unified_console_scripts_use_one_registered_service():
    start = (SCRIPTS / "start_cobot_data_ui.sh").read_text(encoding="utf-8")
    check = (SCRIPTS / "check_cobot_data_ui.sh").read_text(encoding="utf-8")
    stop = (SCRIPTS / "stop_cobot_data_ui.sh").read_text(encoding="utf-8")
    assert "cobot_console.api:app" in start
    assert "cobot-data-console-v1" in start
    assert "8015" in start
    assert "/api/console/identity" in start
    assert "/api/console/identity" in check
    assert "/api/console/status" in check
    assert "active_mode" in stop
    assert "pkill" not in start + check + stop
    assert "killall" not in start + check + stop

def test_unified_console_scripts_are_valid_bash():
    for name in ("start_cobot_data_ui.sh", "check_cobot_data_ui.sh", "stop_cobot_data_ui.sh"):
        subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=True)

def test_legacy_segmented_scripts_delegate_to_unified_lifecycle():
    assert "start_cobot_data_ui.sh" in (SCRIPTS / "start_segmented_capture_v1.sh").read_text(encoding="utf-8")
    assert "check_cobot_data_ui.sh" in (SCRIPTS / "check_segmented_capture_v1.sh").read_text(encoding="utf-8")
    assert "stop_cobot_data_ui.sh" in (SCRIPTS / "stop_segmented_capture_v1.sh").read_text(encoding="utf-8")