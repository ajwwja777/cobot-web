"""Safety and lifecycle tests for the isolated Task5 UI process scripts."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from capture_core.api import create_app
from capture_core.labels import LabelStore

V1_ROOT = Path(__file__).parents[1]
SCRIPTS = V1_ROOT / "scripts"


def test_launcher_defaults_to_current_cobot_wifi_address():
    launcher = (SCRIPTS / "start_task5_v1.sh").read_text(encoding="utf-8")

    assert 'PUBLIC_HOST="${TASK5_PUBLIC_HOST:-10.7.165.64}"' in launcher


def test_launcher_bounds_uvicorn_shutdown_with_open_mjpeg_streams():
    launcher = (SCRIPTS / "start_task5_v1.sh").read_text(encoding="utf-8")

    assert 'GRACEFUL_SHUTDOWN_TIMEOUT="${TASK5_GRACEFUL_SHUTDOWN_SECONDS:-5}"' in launcher
    assert '--timeout-graceful-shutdown "$GRACEFUL_SHUTDOWN_TIMEOUT"' in launcher


def _environment(tmp_path: Path, port: int) -> dict[str, str]:
    ros_setup = tmp_path / "setup.bash"
    ros_setup.write_text("# no-op ROS setup for launcher tests\n", encoding="utf-8")
    return {
        **os.environ,
        "TASK5_RUNTIME_DIR": str(tmp_path / "runtime"),
        "TASK5_LOG_DIR": str(tmp_path / "logs"),
        "TASK5_PYTHON": sys.executable,
        "TASK5_ROS_SETUP": str(ros_setup),
        # Keep launcher lifecycle tests independent of a machine-wide ROS
        # PYTHONPATH.  The production launcher test below separately asserts
        # that the real Noetic setup is sourced.
        "PYTHONPATH": str(V1_ROOT),
        "TASK5_HOST": "127.0.0.1",
        "TASK5_PUBLIC_HOST": "127.0.0.1",
        "TASK5_PORT": str(port),
        "TASK5_START_TIMEOUT_SECONDS": "5",
        "TASK5_STOP_TIMEOUT_SECONDS": "5",
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / script)],
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_frontend_is_served_without_robot_control_surface(tmp_path: Path):
    app = create_app(label_store=LabelStore(tmp_path))
    client = TestClient(app)

    response = client.get("/")
    javascript = client.get("/app.js")

    assert response.status_code == 200
    assert "Human-in-the-Loop Rollout Recorder" in response.text
    assert "TASK5 · V1" in response.text
    assert "JIAAN" not in response.text
    assert response.text.index("1. 实验与保存目录") < response.text.index(
        "2. 实时状态与相机"
    )
    assert "人工中止" not in response.text
    assert "数据质量" not in response.text
    assert "是否纳入训练" not in response.text
    assert "永久删除" in response.text
    assert "删除末尾 episode 后，该末尾编号可由下一条复用" in response.text
    assert "编号不会复用" not in response.text
    assert "<video" in response.text
    assert javascript.status_code == 200
    combined = response.text + javascript.text
    for forbidden in ("/master/", "rospy.Publisher", "ServiceProxy", "homing"):
        assert forbidden not in combined


def test_start_refuses_an_unmanaged_listener_without_stopping_it(tmp_path: Path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        result = _run("start_task5_v1.sh", _environment(tmp_path, port))

        assert result.returncode != 0
        assert "already in use" in result.stderr
        assert listener.fileno() >= 0
        assert not (tmp_path / "runtime" / "task5.pid").exists()


def test_stop_archives_unmanaged_pid_and_sends_no_signal(tmp_path: Path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "task5.pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    env = _environment(tmp_path, _free_port())

    result = _run("stop_task5_v1.sh", env)

    assert result.returncode != 0
    os.kill(os.getpid(), 0)
    assert not (runtime / "task5.pid").exists()
    assert list((runtime / "archive").glob("task5.pid.*"))


def test_start_check_stop_manage_only_the_task5_uvicorn_process(tmp_path: Path):
    port = _free_port()
    env = _environment(tmp_path, port)
    env.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "http_proxy": "http://127.0.0.1:1",
            "https_proxy": "http://127.0.0.1:1",
            "NO_PROXY": "",
            "no_proxy": "",
        }
    )

    start = _run("start_task5_v1.sh", env)
    try:
        assert start.returncode == 0, start.stderr
        assert f"http://127.0.0.1:{port}/" in start.stdout
        check = _run("check_task5_v1.sh", env)
        assert check.returncode == 0, check.stderr
        assert '"status": "not_ready"' in check.stdout
    finally:
        stop = _run("stop_task5_v1.sh", env)
    assert stop.returncode == 0, stop.stderr
    assert not (tmp_path / "runtime" / "task5.pid").exists()
    assert list((tmp_path / "runtime" / "archive").glob("task5.pid.*"))


def test_start_ignores_closed_socket_time_wait(tmp_path: Path):
    """Catches a closed connection being mistaken for a live port listener."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _address = listener.accept()
        accepted.close()
        client.close()

    env = _environment(tmp_path, port)
    start = _run("start_task5_v1.sh", env)
    try:
        assert start.returncode == 0, start.stderr
    finally:
        stop = _run("stop_task5_v1.sh", env)
    assert stop.returncode == 0, stop.stderr


def test_stop_terminates_a_verified_managed_cmdline(tmp_path: Path):
    process = subprocess.Popen(
        [
            "bash",
            "-c",
            'exec -a "python -m uvicorn capture_core.api:app" sleep 60',
        ]
    )
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "task5.pid").write_text(f"{process.pid}\n", encoding="utf-8")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        cmdline = Path(f"/proc/{process.pid}/cmdline")
        if cmdline.exists() and b"capture_core.api:app" in cmdline.read_bytes():
            break
        time.sleep(0.01)

    result = _run("stop_task5_v1.sh", _environment(tmp_path, _free_port()))
    process.wait(timeout=2)

    assert result.returncode == 0, result.stderr
    assert process.returncode is not None
    assert not (runtime / "task5.pid").exists()
