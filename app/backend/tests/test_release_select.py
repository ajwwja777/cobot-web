"""Tests for selecting the RLT actor release from the console."""
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cobot_console.api import create_app
from cobot_console.release_select import ReleaseSelectionError, ReleaseSelector
from tests.test_console_api import FakeBackend, FakeBridge, FakeRecorder, FakeRegistry, FakeSegmentedService, _fresh_cache


def _project(tmp_path: Path) -> Path:
    releases = tmp_path / "runs/plug_v2/learning/rtc-v5/releases"
    releases.mkdir(parents=True)
    for name, value in {
        "rtc-rollback-8185-x.json": {"actor_updates": 4092, "global_step": 8185, "rollback_of": "old.json"},
        "rtc-round-2.json": {"actor_updates": 5092, "global_step": 12185, "advantage": "q", "awbc_beta": 1e6},
        "rtc-round-3.json": {"actor_updates": 5092, "global_step": 12185, "advantage": "v", "awbc_beta": .1},
        "rtc-online-20260921-1.json": {"actor_updates": 7067, "global_step": 14135},
    }.items():
        (releases / name).write_text(json.dumps(value))
    (tmp_path / "runs/plug_v2/learning/rtc-v5/current.json").write_text(
        json.dumps({"release": str(releases / "rtc-rollback-8185-x.json")}))
    return tmp_path


class Runner:
    def __init__(self, fail_step=None):
        self.calls, self.fail_step = [], fail_step

    def __call__(self, command, cwd, env):
        self.calls.append(list(command))
        failed = self.fail_step is not None and self.fail_step in command
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "boom" if failed else "")


def test_listing_shows_selectable_releases_with_current_first_and_readable_kind(tmp_path):
    listing = ReleaseSelector(_project(tmp_path)).listing()
    names = [item["name"] for item in listing["releases"]]
    assert names[0] == "rtc-rollback-8185-x.json" and listing["current"] == names[0]
    assert "rtc-online-20260921-1.json" not in names
    kinds = {item["name"]: item["kind"] for item in listing["releases"]}
    assert kinds == {"rtc-rollback-8185-x.json": "回滚基线", "rtc-round-2.json": "只模仿（不用 critic）",
                     "rtc-round-3.json": "AWBC · V 加权"}


def test_switch_runs_validated_switch_then_session_restart(tmp_path):
    runner = Runner()
    result = ReleaseSelector(_project(tmp_path), runner=runner).switch("rtc-round-2.json", "disarmed")
    assert result["changed"] is True
    assert runner.calls[0][-2:] == ["switch", "rtc-round-2.json"]
    assert runner.calls[1][-4:] == ["up", "--actor", "warmup", "--restart"]


@pytest.mark.parametrize("name,phase,error", [
    ("../../etc/passwd", "disarmed", "release_not_selectable"),
    ("rtc-online-20260921-1.json", "disarmed", "release_not_selectable"),
    ("rtc-round-9.json", "disarmed", "release_not_found"),
    ("rtc-round-2.json", "rollout", "switch_between_episodes_only"),
    ("rtc-round-2.json", "terminal_pending", "switch_between_episodes_only"),
])
def test_switch_rejects_unsafe_requests_without_running_anything(tmp_path, name, phase, error):
    runner = Runner()
    with pytest.raises(ReleaseSelectionError, match=error):
        ReleaseSelector(_project(tmp_path), runner=runner).switch(name, phase)
    assert runner.calls == []


def test_selecting_current_release_is_a_no_op_and_failed_step_stops(tmp_path):
    runner = Runner()
    project = _project(tmp_path)
    selector = ReleaseSelector(project, runner=runner)
    assert selector.switch("rtc-rollback-8185-x.json", "disarmed")["changed"] is False and runner.calls == []
    failing = Runner(fail_step="switch")
    with pytest.raises(ReleaseSelectionError, match="switch_failed"):
        ReleaseSelector(project, runner=failing).switch("rtc-round-3.json", "waiting_scene")
    assert len(failing.calls) == 1  # the Session is not restarted after a failed switch


class FakeSelector:
    def __init__(self):
        self.requests = []

    def listing(self):
        return {"current": "a.json", "releases": [], "switch_phases": []}

    def switch(self, name, phase):
        self.requests.append((name, phase))
        if name == "bad.json":
            raise ReleaseSelectionError("release_not_selectable")
        return {"release": name, "changed": True}


def test_console_routes_list_and_switch_only_in_rlt_mode(tmp_path):
    selector = FakeSelector()
    app = create_app(
        cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry("ready_disarmed"), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path, monotonic=lambda: 10.0, release_selector=selector,
    )
    with TestClient(app) as client:
        assert client.get("/api/rlt/releases").json()["current"] == "a.json"
        assert client.post("/api/rlt/release", json={"release": "x.json"}).status_code == 409  # normal mode
        assert client.post("/api/console/mode", json={"mode": "rlt"}).status_code == 200
        response = client.post("/api/rlt/release", json={"release": "x.json"})
        assert response.status_code == 200 and response.json()["changed"] is True
        assert client.post("/api/rlt/release", json={"release": "bad.json"}).status_code == 409
        assert client.post("/api/rlt/release", json={"release": "x.json", "extra": 1}).status_code == 422
    assert [name for name, _ in selector.requests] == ["x.json", "bad.json"]
