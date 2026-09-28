import os
import subprocess
import sys
import time
import pytest
from cobot_console.deployment import ManagedRuntime, DeploymentError
from cobot_console import device_control
def test_external_foreground_group_owned_and_duplicate_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(device_control, "_default_process_finder", lambda marker: [])
    script=tmp_path/"server.py"
    script.write_text("import time\nprint('server process only', flush=True)\ntime.sleep(120)\n")
    unrelated=subprocess.Popen([sys.executable,"-c","import time; time.sleep(120)"],start_new_session=True)
    runtime=ManagedRuntime(tmp_path/"runtime")
    model={"id":"fixture","kind":"external","command":[sys.executable,str(script)],
        "cwd":str(tmp_path),"capabilities":{"load":True,"pause":False,"resume":False,"start":False}}
    try:
        state=runtime.load(model)
        assert state["phase"]=="process_running"
        assert state["model_ready"] is False and state["inference_verified"] is False
        second=ManagedRuntime(tmp_path/"runtime")
        with pytest.raises(DeploymentError): second.load(model)
        with pytest.raises(DeploymentError): second.action("pause")
        assert second.unload()["phase"]=="offline"
        assert unrelated.poll() is None
    finally:
        if runtime.process and runtime.process.poll() is None:
            os.killpg(runtime.process.pid, 15);runtime.process.wait(timeout=5)
        unrelated.terminate();unrelated.wait(timeout=5)
