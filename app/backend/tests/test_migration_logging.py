import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from cobot_console.task_outputs import important_output

ROOT=Path(__file__).resolve().parents[3]
def test_concurrent_ros_log_updates_are_atomic_and_keep_real_directories(tmp_path):
    spec=importlib.util.spec_from_file_location("logging_compat", ROOT/"scripts/ros_log_compat/sitecustomize.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    runs=[tmp_path/str(i) for i in range(20)]
    for p in runs:p.mkdir()
    with ThreadPoolExecutor(max_workers=20) as pool:
        assert all(pool.map(lambda p:module.renew_latest_logdir(str(p)),runs*10))
    assert (tmp_path/"latest").is_symlink() and (tmp_path/"latest").exists()
    (tmp_path/"latest").unlink();(tmp_path/"latest").mkdir()
    assert module.renew_latest_logdir(str(runs[0])) is False

def test_concise_output_preserves_real_camera_errors_and_raw_input():
    raw="check device status 1\n[ERROR] Not a JPEG file\n[ERROR] Not a JPEG file\n"
    out=important_output("cameras","running",raw,{"cameras":{"phase":"ready"}})
    assert "Not a JPEG file [x2]" in out["en"] and "check device status" not in out["en"]
    assert "[ERROR]" in raw
    out=important_output("arms","running","INFO: cannot create a symlink to latest log directory: [Errno 2] No such file or directory",{"arms":{"phase":"ready"}})
    assert "Arms: nodes ready" in out["en"] and "cannot create" not in out["en"]


def test_log_filter_does_not_hide_real_filesystem_failures():
    raw="INFO: cannot create a symlink to latest log directory: [Errno 13] Permission denied"
    output=important_output("arms","running",raw,{"arms":{"phase":"ready"}})
    assert "Permission denied" in output["en"]
