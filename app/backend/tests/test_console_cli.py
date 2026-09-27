"""Exercise terminal request contracts without controlling hardware."""
import importlib.util
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

spec = importlib.util.spec_from_file_location("console_cli", Path(__file__).parents[3] / "scripts/console.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def request(self, method, path, body=None, *args):
        self.calls.append((method, path, body))
        return next(self.replies)


def args(*items):
    return cli.build_parser().parse_args(items)


def test_manual_capture_uses_flat_storage_and_no_model():
    c = FakeClient([{}, {"capture_state": "recording"}])
    result = cli.capture(c, args("capture", "start", "--data-root", "/data/demo"))
    assert result["capture_state"] == "recording"
    method, path, body = c.calls[-1]
    assert (method, path) == ("POST", "/api/segmented-teach/start")
    assert body["storage_layout"] == "flat" and body["use_model"] is False
    assert body["data_root"] == "/data/demo"


def test_normal_save_uses_current_identity_and_unknown_label():
    c = FakeClient([{"active_mode": "normal"}, {"episode_uuid": "u1", "generation": 5}, {}])
    cli.capture(c, args("capture", "save"))
    assert c.calls[-1] == ("POST", "/api/segmented-teach/stop",
                           {"episode_uuid": "u1", "generation": 5, "outcome": "unknown"})


def test_rlt_failure_uses_fresh_identity_and_does_not_home():
    c = FakeClient([{"selected_mode": "rlt"}, {"episode_id": "u2", "generation": "g2"}, {}])
    cli.capture(c, args("capture", "failure"))
    assert c.calls[-1] == ("POST", "/api/rlt/episode/failure",
                           {"episode_id": "u2", "generation": "g2", "home_after_terminal": False})


def test_waiting_scene_starts_next_episode():
    c = FakeClient([{"phase": "paused", "model": {"id": "m1", "kind": "rlt"}}, {},
                    {"phase": "waiting_scene", "episode_id": "u3", "generation": "g3"}, {}])
    cli.capture(c, args("capture", "start", "--model", "m1"))
    assert c.calls[-1] == ("POST", "/api/rlt/episode/next", {"episode_id": "u3", "generation": "g3"})


def test_start_rejects_a_different_loaded_model_before_any_post():
    c = FakeClient([{"phase": "paused", "model": {"id": "different"}}])
    with pytest.raises(cli.ConsoleError, match="Load"):
        cli.capture(c, args("capture", "start", "--model", "m1"))
    assert all(method == "GET" for method, _, _ in c.calls)


def test_device_uses_same_confirmation_token_without_shell_commands():
    c = FakeClient([{"confirmation_token": "token1"}, {"phase": "running"}])
    cli.device(c, args("device", "home", "run", "--target", "selection",
                       "--arms", "mid,front-right", "--pose", "plug2"))
    assert c.calls[0][1] == "/api/console/devices/confirm"
    assert c.calls[1][2] == {"confirmation_token": "token1", "operation": {
        "component": "home", "action": "run", "target": "selection",
        "arms": ["mid", "front-right"], "pose": "plug2"}}


def test_http_mutation_failure_is_not_retried():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"detail": "operator_nodes mismatch"}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        client = cli.Client("http://127.0.0.1:%s" % server.server_port)
        with pytest.raises(cli.ConsoleError, match="结果待确认"):
            client.request("POST", "/api/example", {})
        assert requests == ["/api/example"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_recovery_arguments_preserve_execute_flags():
    parsed = args("recovery", "interrupt", "model", "--execute", "--robot-stopped")
    assert parsed.args == ["interrupt", "model", "--execute", "--robot-stopped"]


def test_missing_episode_identity_refuses_mutation():
    with pytest.raises(cli.ConsoleError, match="identity"):
        cli.version({"generation": 5})


def test_model_wait_does_not_confuse_pending_load_with_offline(monkeypatch):
    c = FakeClient([{"phase": "offline", "operation": "load"}, {"phase": "paused", "operation": None}])
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: None)
    assert cli.wait_model(c, 10)["phase"] == "paused"
