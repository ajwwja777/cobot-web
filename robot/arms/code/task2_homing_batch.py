"""Read-only participant barrier for an operator-requested four-arm home."""
import time

ROOT = "/task2/homing/batch"

def check_abort(ros):
    active = ros.get_param(ROOT + "/active", "")
    if active and ros.get_param(ROOT + "/abort", False):
        raise RuntimeError("synchronized home cancelled")

def wait_start(ros, participant, can_continue=lambda: True):
    batch = ros.get_param(ROOT + "/active", "")
    if not batch:
        return
    ros.set_param(ROOT + "/ready/" + participant, batch)
    deadline = time.monotonic() + 30.0
    while True:
        if ros.is_shutdown() or not can_continue():
            raise RuntimeError("home preparation interrupted")
        if ros.get_param(ROOT + "/active", "") != batch:
            raise RuntimeError("home batch changed")
        check_abort(ros)
        start = float(ros.get_param(ROOT + "/start_at", 0.0))
        if start > 0 and time.time() >= start:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("synchronized home readiness timeout")
        time.sleep(0.01)
