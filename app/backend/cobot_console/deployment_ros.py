"""Bounded ROS command bridge; repair-pause never requests robot motion."""
import json
import fcntl
from contextlib import contextmanager
import os
import subprocess
import sys
import time
from pathlib import Path


def deployment_directory():
    root = Path(__file__).resolve().parents[3]
    config_path = Path(os.environ.get("COBOT_HOST_CONFIG", root / "configs/local.json"))
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    runtime = Path(os.environ.get("COBOT_RUNTIME_ROOT") or config.get("runtime_root") or root / "runtime")
    return runtime / "deployment"


def legacy_gate(directory):
    saved = json.loads((directory / "process.json").read_text())
    if saved.get("model", {}).get("kind") != "pi05":
        raise RuntimeError("Pause repair only supports the loaded legacy pi05 client")
    pid = int(saved["pid"])
    ticks = Path("/proc/{}/stat".format(pid)).read_text().rsplit(")", 1)[1].split()[19]
    if int(ticks) != int(saved["start_ticks"]):
        raise RuntimeError("Model process identity changed")
    state = json.loads((directory / "pi05-gate.json").read_text())
    if state.get("schema_version", 1) != 1:
        raise RuntimeError("Client already has the corrected pause protocol")
    if not state.get("paused"):
        raise RuntimeError("Pause the model before repairing the old pause latch")
    return state


def require_idle_handover(rospy, string_type):
    topics = (
        ("/task2/teach_handover/mode", "policy"),
        ("/task2/teach_handover/fault", ""),
        ("/task2/teach/rear_left/fault", ""),
        ("/task2/teach/rear_right/fault", ""),
        ("/task2/teach_handover/mode", "policy"),
    )
    deadline = time.monotonic() + 1.5
    for topic, expected in topics:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Handover status timed out; keep paused")
        value = rospy.wait_for_message(topic, string_type, timeout=remaining).data
        if value != expected:
            raise RuntimeError("Pause repair blocked: {} = {!r}".format(topic, value))


def require_pi05_service(rospy):
    import rosgraph
    services = rosgraph.Master(rospy.get_name()).getSystemState()[2]
    owners = dict(services).get("/task2/policy/set_paused", [])
    if len(owners) != 1 or not owners[0].startswith("/pi05_cobot_inference_"):
        raise RuntimeError("Pause repair blocked: unexpected pause service owner")
    return owners[0]


def repair_legacy_pause(rospy, set_bool, string_type, directory, child=False):
    owner = require_pi05_service(rospy)
    before = legacy_gate(directory)
    pause = rospy.ServiceProxy("/task2/policy/set_paused", set_bool)
    if child:
        # Parent holds the pause-command lock and has latched manual pause.
        # This caller clears only the old external latch; effective pause MUST stay true.
        if not before.get("manual_pause"):
            raise RuntimeError("Manual pause must remain held throughout repair")
        require_idle_handover(rospy, string_type)
        result = pause(False)
        if not result.success or not result.message.startswith("paused"):
            pause(True)
            raise RuntimeError("Legacy repair did not preserve effective pause")
    else:
        legacy_gate(directory)
        result = pause(True)  # This node has the operator caller identity.
        if not result.success:
            raise RuntimeError(result.message)
        require_idle_handover(rospy, string_type)
        held = legacy_gate(directory)
        if not held.get("manual_pause"):
            raise RuntimeError("Could not hold manual pause")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "_repair-held-pause"],
            capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
        after = legacy_gate(directory)
        if not after.get("manual_pause") or require_pi05_service(rospy) != owner:
            raise RuntimeError("Pause ownership changed during repair")
        require_idle_handover(rospy, string_type)
        if after["intervention_count"] != before["intervention_count"]:
            raise RuntimeError("A new handover arrived; keep paused and inspect the controls")
        return {"success": True, "message": "Legacy latch cleared; manually paused. Continue only when ready."}
    after = legacy_gate(directory)
    if not after.get("manual_pause") or require_pi05_service(rospy) != owner:
        raise RuntimeError("Pause ownership changed during repair")
    return {"success": True, "message": "Manual pause preserved"}


@contextmanager
def command_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".pause-command.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_pi05(directory):
    registry = directory / "process.json"
    if not registry.exists():
        return False
    saved = json.loads(registry.read_text())
    return saved.get("model", {}).get("kind") == "pi05"


def legacy_resume_needed(directory):
    if not is_pi05(directory):
        return False
    gate = json.loads((directory / "pi05-gate.json").read_text())
    return gate.get("schema_version", 1) == 1 and gate.get("paused") is True


def command(rospy, set_bool, trigger, string_type, action, arm=False):
    rospy.wait_for_service("/task2/policy/set_paused", timeout=3)
    if action in {"repair-pause", "_repair-held-pause"}:
        return repair_legacy_pause(rospy, set_bool, string_type, deployment_directory(),
                                   child=action == "_repair-held-pause")
    if action not in {"pause", "resume"}:
        raise RuntimeError("Use pause, resume, or repair-pause")
    if arm and action != "pause":
        mode = rospy.wait_for_message("/task2/teach_handover/mode", string_type, timeout=3)
        if mode.data != "policy":
            raise RuntimeError("Finish teaching before starting inference")
        rospy.wait_for_service("/task2/policy/arm", timeout=3)
        armed = rospy.ServiceProxy("/task2/policy/arm", trigger)()
        if not armed.success:
            raise RuntimeError(armed.message)
    # This only runs for an explicit Start/Continue, never for load or polling.
    # Current already-loaded clients can therefore recover without reloading weights.
    if action == "resume" and not arm:
        directory = deployment_directory()
        if is_pi05(directory):
            # Recover may have retired a fault/HIL latch, but actual teaching
            # or a remaining hardware coordinator fault must still block Start.
            require_idle_handover(rospy, string_type)
        if legacy_resume_needed(directory):
            repair_legacy_pause(rospy, set_bool, string_type, directory)
    result = rospy.ServiceProxy("/task2/policy/set_paused", set_bool)(action == "pause")
    success, message = bool(result.success), result.message
    # Old pi05 returned success even when another latch prevented resume.
    if action == "resume" and success and message.startswith("paused"):
        success = False
        message = "Model remains paused by handover/protective latch; inspect controls (legacy pi05 after Recover: repair-pause)"
    return {"success": success, "message": message}


def main():
    import rospy
    from std_srvs.srv import SetBool, Trigger
    from std_msgs.msg import String
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    node = "cobot_pi05_pause_repair" if action == "_repair-held-pause" else "cobot_deployment_command"
    rospy.init_node(node, anonymous=True, disable_signals=True)
    try:
        directory = deployment_directory()
        if action == "_repair-held-pause":
            # The parent owns both locks; this helper must never acquire them.
            result = command(rospy, SetBool, Trigger, String, action)
        elif action == "repair-pause":
            from runtime_lock import operation
            with operation(directory), command_lock(directory):
                result = command(rospy, SetBool, Trigger, String, action)
        else:
            # Web/console ManagedRuntime already holds the model operation lock.
            # A separate command lock also covers direct ROS-bridge CLI calls.
            with command_lock(directory):
                result = command(rospy, SetBool, Trigger, String, action, "--arm" in sys.argv)
    except Exception as exc:
        result = {"success": False, "message": str(exc)}
    print(json.dumps(result))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
