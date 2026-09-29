"""Regression: Recover must not masquerade as a held teaching button."""
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import cobot_console
from cobot_console import deployment_ros as bridge


@pytest.fixture
def gate(monkeypatch, tmp_path):
    class OriginalGate:
        def __init__(self):
            self._lock = threading.RLock()
            self.paused = True

        def handle_set_paused(self, request):
            self.paused = request.data
            return SimpleNamespace(success=True, message="paused" if self.paused else "fresh resume")

    monkeypatch.setitem(sys.modules, "inference_pi05_rtc_task2",
                        SimpleNamespace(Task2PauseGate=OriginalGate))
    monkeypatch.setenv("COBOT_PI05_CLIENT_ROOT", str(tmp_path))
    monkeypatch.setenv("COBOT_PI05_GATE_STATE", str(tmp_path / "gate.json"))
    script = Path(cobot_console.__file__).parent / "deployment_pi05_client.py"
    spec = importlib.util.spec_from_file_location("pi05_gate_regression", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ConsolePauseGate()


def send(gate, value, caller="/cobot_deployment_command_42"):
    return gate.handle_set_paused(SimpleNamespace(
        data=value, _connection_header={"callerid": caller}))


@pytest.mark.parametrize("caller", ["/task2_recover_cli_123", "/platform_front_pair_recovery_12", "/rosservice_45"])
def test_recover_protects_without_sticking_hil(gate, caller):
    assert send(gate, False).success and not gate.paused
    assert send(gate, True, caller).success and gate.paused
    assert gate.manual_pause and not gate.hil_active
    assert gate.intervention_count == 0
    saved = json.loads(gate.path.read_text())
    assert saved["pause_caller"] == caller and saved["pause_source"] == "protective"
    assert saved["schema_version"] == 2
    assert send(gate, False).success and not gate.paused


def test_actual_teaching_still_blocks_resume_and_counts_only_edges(gate):
    assert send(gate, False).success
    for _ in range(3):
        assert send(gate, True, "/task2_teach_button_handover").success
    result = send(gate, False)
    assert not result.success and gate.paused and gate.hil_active
    assert "Teaching" in result.message
    assert gate.intervention_count == 1
    assert send(gate, False, "/task2_teach_button_handover").success
    assert not gate.paused


def test_recover_pause_survives_teaching_release(gate):
    send(gate, False)
    send(gate, True, "/task2_teach_button_handover")
    send(gate, True, "/task2_recover_cli_123")
    send(gate, False, "/task2_teach_button_handover")
    assert gate.paused and gate.manual_pause and not gate.hil_active
    assert send(gate, False).success and not gate.paused


@pytest.mark.parametrize("caller", ["", "/task2_recover_cli_123", "/not_cobot_deployment_command_123"])
def test_untrusted_resume_does_not_clear_protection(gate, caller):
    result = send(gate, False, caller)
    assert not result.success and gate.paused and gate.manual_pause


def fake_ros(mode="policy", fault=""):
    calls = []
    def request(value):
        calls.append(value)
        return SimpleNamespace(success=True, message="paused (generation 42)")
    return SimpleNamespace(
        wait_for_service=lambda *a, **k: None,
        wait_for_message=lambda topic, *_a, **_k: SimpleNamespace(
            data=mode if topic.endswith("/mode") else fault),
        ServiceProxy=lambda *a: request,
        calls=calls,
    )


def test_bridge_does_not_claim_legacy_blocked_resume_succeeded(monkeypatch):
    monkeypatch.setattr(bridge, "legacy_resume_needed", lambda _: False)
    ros = fake_ros()
    result = bridge.command(ros, object, object, object, "resume")
    assert not result["success"]
    assert "remains paused" in result["message"]


@pytest.mark.parametrize("mode,fault", [("manual:left", ""), ("fault", ""), ("policy", "rear driver error")])
def test_repair_refuses_real_teaching_or_fault(monkeypatch, tmp_path, mode, fault):
    ros = fake_ros(mode, fault)
    monkeypatch.setattr(bridge, "legacy_gate", lambda _: {"paused": True, "manual_pause": True, "intervention_count": 1})
    monkeypatch.setattr(bridge, "require_pi05_service", lambda _: "/pi05_cobot_inference_1")
    sys.path.insert(0, str(Path(bridge.__file__).parent))
    try:
        with pytest.raises(RuntimeError, match="blocked"):
            bridge.repair_legacy_pause(ros, object, object, tmp_path, child=True)
    finally:
        sys.path.pop(0)
    assert ros.calls == []


def test_repair_holds_manual_pause_and_never_resumes(monkeypatch, tmp_path):
    ros = fake_ros()
    state = {"paused": True, "manual_pause": False, "intervention_count": 1}
    monkeypatch.setattr(bridge, "legacy_gate", lambda _: dict(state))
    monkeypatch.setattr(bridge, "require_pi05_service", lambda _: "/pi05_cobot_inference_1")
    def request(value):
        ros.calls.append(value)
        state["manual_pause"] = value
        return SimpleNamespace(success=True, message="paused (generation 43)")
    ros.ServiceProxy = lambda *a: request
    def child(*args, **kwargs):
        assert state["manual_pause"] and state["paused"]
        assert args[0][-1] == "_repair-held-pause"
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(bridge.subprocess, "run", child)
    sys.path.insert(0, str(Path(bridge.__file__).parent))
    try:
        result = bridge.repair_legacy_pause(ros, object, object, tmp_path)
    finally:
        sys.path.pop(0)
    assert result["success"] and ros.calls == [True]
    assert state["paused"] and state["manual_pause"]


def test_child_refuses_without_manual_hold(monkeypatch, tmp_path):
    ros = fake_ros()
    monkeypatch.setattr(bridge, "legacy_gate", lambda _: {"paused": True, "manual_pause": False})
    monkeypatch.setattr(bridge, "require_pi05_service", lambda _: "/pi05_cobot_inference_1")
    with pytest.raises(RuntimeError, match="Manual pause"):
        bridge.repair_legacy_pause(ros, object, object, tmp_path, child=True)
    assert ros.calls == []


def test_child_clears_only_legacy_hil_latch(monkeypatch, tmp_path):
    ros = fake_ros()
    monkeypatch.setattr(bridge, "legacy_gate", lambda _: {"paused": True, "manual_pause": True})
    monkeypatch.setattr(bridge, "require_pi05_service", lambda _: "/pi05_cobot_inference_1")
    result = bridge.repair_legacy_pause(ros, object, object, tmp_path, child=True)
    assert result["success"] and ros.calls == [False]


def test_new_protocol_and_non_pi05_refuse_legacy_repair(tmp_path):
    proc = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19]
    import os
    registry = {"model": {"kind": "pi05"}, "pid": os.getpid(), "start_ticks": int(proc)}
    (tmp_path / "process.json").write_text(json.dumps(registry))
    (tmp_path / "pi05-gate.json").write_text(json.dumps({"schema_version": 2, "paused": True}))
    with pytest.raises(RuntimeError, match="corrected"):
        bridge.legacy_gate(tmp_path)
    registry["model"]["kind"] = "rlt"
    (tmp_path / "process.json").write_text(json.dumps(registry))
    with pytest.raises(RuntimeError, match="only supports"):
        bridge.legacy_gate(tmp_path)


def test_explicit_legacy_resume_reconciles_before_unpausing(monkeypatch, tmp_path):
    ros = fake_ros()
    calls = []
    monkeypatch.setattr(bridge, "deployment_directory", lambda: tmp_path)
    monkeypatch.setattr(bridge, "legacy_resume_needed", lambda _: True)
    def repair(*args, **kwargs):
        calls.append("repair-held-pause")
        return {"success": True}
    def request(value):
        calls.append(value)
        return SimpleNamespace(success=True, message="fresh resume")
    ros.ServiceProxy = lambda *args: request
    monkeypatch.setattr(bridge, "repair_legacy_pause", repair)
    result = bridge.command(ros, object, object, object, "resume")
    assert result["success"] and calls == ["repair-held-pause", False]


def test_blocked_legacy_reconciliation_never_resumes(monkeypatch, tmp_path):
    ros = fake_ros()
    monkeypatch.setattr(bridge, "deployment_directory", lambda: tmp_path)
    monkeypatch.setattr(bridge, "legacy_resume_needed", lambda _: True)
    def blocked(*args, **kwargs):
        raise RuntimeError("Pause repair blocked: manual:left")
    monkeypatch.setattr(bridge, "repair_legacy_pause", blocked)
    with pytest.raises(RuntimeError, match="blocked"):
        bridge.command(ros, object, object, object, "resume")
    assert ros.calls == []


def test_pause_does_not_reconcile_or_resume(monkeypatch):
    ros = fake_ros()
    def unexpected(*args):
        raise AssertionError("Pause must not touch the legacy latch")
    monkeypatch.setattr(bridge, "legacy_resume_needed", unexpected)
    result = bridge.command(ros, object, object, object, "pause")
    assert result["success"] and ros.calls == [True]
