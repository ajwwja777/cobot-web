"""Allow UI shutdown for a completed orphan; never interrupt a live writer."""
def completed_orphan(console, model, recorder):
    from .deployment import ManagedRuntime
    from .paths import RUNTIME_ROOT
    if (console.get("active_mode") != "rlt" or model.get("phase") != "error"
            or model.get("model", {}).get("kind") != "rlt"
            or model.get("active") or model.get("operation") or model.get("status_stale")
            or recorder.get("state") != "stopped" or recorder.get("active")
            or recorder.get("publication_status") != "committed"
            or recorder.get("completion_state") != "complete"
            or recorder.get("writer_thread_alive") or recorder.get("acquisition_active")):
        return False
    # Verify local process identities/groups, not just an old HTTP phase.
    runtime = ManagedRuntime(RUNTIME_ROOT / "deployment")
    return bool(model.get("pid") and model.get("start_ticks") is not None
                and not runtime._alive(model) and not runtime._owned_members(model))
