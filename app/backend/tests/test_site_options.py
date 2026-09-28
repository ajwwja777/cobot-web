import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cobot_console import site_options as site
from cobot_console.device_control import DeviceController


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(site, "MODEL_ROOT", tmp_path / "model")
    monkeypatch.setattr(site, "roots", lambda: [tmp_path])
    site.MODEL_ROOT.mkdir()
    return tmp_path


def actor(root):
    folder = root / "actor_snapshot"
    folder.mkdir(parents=True)
    (folder / "actor_snapshot.pkl").write_bytes(b"not deserialized")
    (root / "action_norm_stats.json").write_text("{}")
    return folder / "actor_snapshot.pkl"


def test_register_checks_contract_persists_and_never_deserializes(configured):
    checkpoint = actor(site.MODEL_ROOT / "candidate")
    with pytest.raises(ValueError, match="Confirm"):
        site.register("plug-v3-warmup-5k", str(checkpoint), "candidate", False)
    identifier = site.register("plug-v3-warmup-5k", str(checkpoint), "candidate", True)
    assert site.read()["models"][0]["id"] == identifier
    before = checkpoint.read_bytes()
    discovered = site.discover()
    assert any(row["checkpoint"] == str(checkpoint) for row in discovered)
    assert checkpoint.read_bytes() == before
    (checkpoint.parent.parent / "action_norm_stats.json").unlink()
    with pytest.raises(ValueError, match="Missing matching"):
        site.register("plug-v3-warmup-5k", str(checkpoint), "", True)


def test_browse_blocks_symlink_escape_and_pi05_missing_assets(configured, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (configured / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside configured"):
        site.browse(str(configured / "escape"))
    assert not any(row["name"] == "escape" for row in site.browse(str(configured))["entries"])
    with pytest.raises(ValueError, match="Missing matching"):
        site.validate_checkpoint("pi05-in-the-pot", str(site.MODEL_ROOT))


def test_custom_launch_has_stable_marker_and_exact_argv(configured):
    script = configured / "launch $(literal).sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    site.save_hardware("arms", str(script), args=["name:=with space", "$(literal)"])
    controller = DeviceController(configured / "jobs", system_probe=lambda: {})
    command = controller._command({"component": "arms", "action": "start"})
    assert command[-2:] == ["name:=with space", "$(literal)"]
    assert command[command.index("--path") + 1] == str(script)
    assert controller._marker("arms") == str(script)
    from cobot_console.terminal_commands import task_terminal_details
    recipe = task_terminal_details({"command": command})["terminal_command"]
    assert len(recipe.splitlines()) == 2
    subprocess.run(["bash", "-n"], input=recipe, text=True, check=True)
    assert "'$(literal)'" in recipe and "'./launch $(literal).sh'" in recipe
    site.save_hardware("arms", "")
    assert controller._command({"component": "arms", "action": "start"})[0].endswith("/scripts/arms_up.sh")


def test_configuration_routes_do_not_start_jobs_and_block_active_tasks(configured):
    checkpoint = actor(site.MODEL_ROOT / "candidate")
    class Manager:
        lock = threading.RLock()
        directory = configured / "deployment"
        settings = {}
        models = []
        phase = "offline"
        busy = lambda self: False
        def status(self): return {"phase": self.phase}
        def refresh_catalog(self):
            self.models = [{"id": row["id"], "checkpoint": row["checkpoint"], "available": True}
                           for row in site.read().get("models", [])]
    manager = Manager()
    devices = DeviceController(configured / "jobs", launcher=lambda *a: pytest.fail("must not launch"),
                               system_probe=lambda: {}, process_finder=lambda marker: [])
    app = FastAPI()
    site.install_routes(app, manager, devices)
    with TestClient(app) as client:
        result = client.post("/api/site/models", json={
            "template": "plug-v3-warmup-5k", "checkpoint": str(checkpoint),
            "compatible": True, "make_default": True})
        assert result.status_code == 200, result.text
        assert manager.settings["model_id"] == result.json()["model_id"]
        assert not (configured / "deployment/process.json").exists()
        manager.phase = "running"
        assert client.post("/api/site/default", json={"model_id":manager.settings["model_id"]}).status_code == 409
        manager.phase = "offline"
        script = configured / "custom.sh"
        script.write_text("exit 0")
        devices.status = lambda: {"jobs":{"arms":{"phase":"running"}}}
        assert client.post("/api/site/hardware", json={"component":"arms","path":str(script)}).status_code == 409
        assert site.hardware() == {}


def test_supervisor_interrupt_stops_foreground_child(configured):
    # Real process lifecycle with a harmless sleeper, no ROS or device calls.
    script = configured / "sleep.sh"
    marker = configured / "child.pid"
    script.write_text('echo $$ > "$1"\nexec sleep 60\n')
    runner = Path(__file__).resolve().parents[3] / "scripts/site_device.py"
    process = subprocess.Popen([sys.executable, str(runner), "--component", "arms",
                                "--path", str(script), "--cwd", str(configured), "--", str(marker)],
                               start_new_session=True)
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        child = int(marker.read_text())
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=3)
        assert process.returncode in (0, 130)
        assert not Path("/proc/" + str(child)).exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def test_registered_model_forwards_selected_weight_to_existing_adapter(configured, monkeypatch):
    from cobot_console import deployment
    calls = []
    monkeypatch.setattr(deployment.subprocess, "Popen", lambda command, **kw:
                        (calls.append((command, kw)) or SimpleNamespace(pid=999999)))
    monkeypatch.setattr("cobot_console.device_control._default_process_finder", lambda marker: [])
    runtime = deployment.ManagedRuntime(configured / "runtime")
    monkeypatch.setattr(runtime, "status", lambda: {})
    model = {"id":"custom-123", "kind":"pi05", "custom":True,
             "adapter_id":"pi05-in-the-pot", "checkpoint":"/chosen/model"}
    runtime.load(model)
    assert calls[0][0][-1] == "custom-123"
    assert calls[0][1]["env"]["COBOT_CUSTOM_CHECKPOINT"] == "/chosen/model"
    assert calls[0][1]["env"]["COBOT_DEPLOYMENT_ADAPTER"] == "pi05-in-the-pot"

def test_metadata_only_asset_is_visible_but_never_available(configured):
    folder = site.MODEL_ROOT / "metadata"
    folder.mkdir()
    (folder / "deployment_manifest.json").write_text("{}")
    row = site.inventory_models([])[0]
    assert not row["available"]
    assert row["unavailable_reason_en"] == "Metadata only; checkpoint missing locally"
