"""Tests for the bounded read-only console diagnostics payload."""
import json
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from cobot_console.api import create_app
from cobot_console.diagnostics import ConsoleDiagnostics
from capture_core.ros_cache import LatestMessageCache
from tests.test_console_api import FakeBackend, FakeBridge, FakeRecorder, FakeRegistry, FakeSegmentedService, _fresh_cache


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_waiting_explains_exact_episode_deficit_and_quarantine(tmp_path):
    _write(tmp_path / "learning/rtc-v5/online_cycle.json", {
        "phase": "waiting", "pending_episodes": 1, "new_since_attempt": 1,
        "quarantined_episodes": 3, "episodes_per_update": 5,
    })
    payload = ConsoleDiagnostics(tmp_path, wall_clock=lambda: 10.0).snapshot(
        LatestMessageCache(), lambda: None
    )
    explanation = payload["learning"]["update_explanation"]
    assert explanation == {
        "code": "waiting_for_episodes",
        "severity": "info",
        "pending": 1,
        "required": 5,
        "missing": 4,
        "quarantined": 3,
    }


def test_metrics_are_bounded_whitelisted_and_nonfinite_rows_are_skipped(tmp_path):
    run = tmp_path / "learning/rtc-online-run"
    release = tmp_path / "learning/rtc-v5/releases/release.json"
    _write(tmp_path / "learning/rtc-v5/current.json", {"release": str(release)})
    _write(release, {
        "name": "release-9", "global_step": 900, "actor_updates": 450,
        "offline_audit": str(run / "release_audit.json"),
        "online_update": {"episodes_per_update": 5},
    })
    run.mkdir(parents=True)
    _write(run / "release_audit.json", {"accepted": True})
    rows = []
    for index in range(250):
        rows.append(json.dumps({
            "global_step": index, "critic_loss": index / 1000,
            "q1_mean": 0.5, "private_field": "omit",
        }))
    rows.insert(30, "{broken")
    rows.insert(50, json.dumps({"global_step": 50, "critic_loss": float("inf")}))
    (run / "metrics.jsonl").write_text("\n".join(rows), encoding="utf-8")
    payload = ConsoleDiagnostics(tmp_path, wall_clock=lambda: 10.0).snapshot(
        LatestMessageCache(), lambda: {"phase": "ready"}
    )
    metrics = payload["learning"]["metrics"]
    assert len(metrics) == 240
    assert metrics[-1]["global_step"] == 249.0
    assert set(metrics[-1]) == {"global_step", "critic_loss", "q1_mean"}
    assert payload["learning"]["release"]["actor_version"] == 450
    assert payload["learning"]["release"]["global_step"] == 900


def test_metrics_after_selected_release_step_are_excluded(tmp_path):
    run=tmp_path/'learning/run'; release=tmp_path/'learning/rtc-v5/releases/release.json'
    _write(tmp_path/'learning/rtc-v5/current.json',{'release':str(release)})
    _write(release,{'global_step':100,'actor_updates':40,'offline_audit':str(run/'release_audit.json')})
    run.mkdir(parents=True); _write(run/'release_audit.json',{})
    (run/'metrics.jsonl').write_text('\n'.join([json.dumps({'global_step':100,'critic_loss':.2}),json.dumps({'global_step':200,'critic_loss':.01})]))
    payload=ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(),lambda:None)
    assert [row['global_step'] for row in payload['learning']['metrics']]==[100.0]


def test_release_pointer_cannot_escape_run_root(tmp_path):
    outside = tmp_path.parent / "outside-release.json"
    _write(outside, {"global_step": 999})
    _write(tmp_path / "learning/rtc-v5/current.json", {"release": str(outside)})
    payload = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)
    assert payload["learning"]["release"] is None
    assert payload["availability"]["release"] == "release_outside_root"


def test_robot_payload_excludes_images_and_reports_joint_freshness(tmp_path):
    cache = LatestMessageCache()
    cache.put("camera_high", np.zeros((20, 20, 3), np.uint8), 9.9, 9.9)
    cache.put("front_right", {
        "position": np.arange(7, dtype=np.float32),
        "velocity": np.arange(7, dtype=np.float32) / 10,
        "effort": np.zeros(7, np.float32),
    }, 9.95, 9.95)
    payload = ConsoleDiagnostics(tmp_path, monotonic=lambda: 10.0).snapshot(cache, lambda: None)
    assert "camera_high" not in payload["robot"]
    assert payload["robot"]["front_right"]["fresh"] is True
    assert payload["robot"]["front_right"]["position"] == [float(x) for x in range(7)]
    assert payload["robot"]["summary"]["max_abs_velocity"] == 0.5


class FakeDiagnostics:
    def snapshot(self, cache, session_loader):
        assert cache is not None
        assert session_loader() == {"phase": "ready", "episode_id": 0, "generation": 3, "policy_paused": True}
        return {"generated_at": 1.0, "learning": {"update_explanation": {"code": "ready"}}}


def test_console_exposes_injected_diagnostics_without_changing_controls(tmp_path):
    app = create_app(
        cache=_fresh_cache(), bridge=FakeBridge(), recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(), backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry("ready_disarmed"), allowed_data_root=tmp_path,
        rlt_data_root=tmp_path, monotonic=lambda: 10.0,
        diagnostics_provider=FakeDiagnostics(),
    )
    with TestClient(app) as client:
        assert client.post("/api/console/mode", json={"mode": "rlt"}).status_code == 200
        response = client.get("/api/console/diagnostics")
    assert response.status_code == 200
    assert response.json()["learning"]["update_explanation"]["code"] == "ready"


def test_payload_summarizes_large_ledgers_and_session(tmp_path):
    _write(tmp_path / "learning/rtc-v5/online_cycle.json", {
        "phase": "waiting", "pending_episodes": 2, "episodes_per_update": 5,
        "consumed_uuids": ["c"] * 1000, "attempted_uuids": ["a"] * 1000,
        "new_uuids": [str(index) for index in range(20)],
    })
    _write(tmp_path / "learning/operation.json", {
        "phase": "rejected", "accepted": False,
        "reasons": ["reason"] * 30, "before": {"huge": list(range(1000))},
        "new_uuids": [str(index) for index in range(20)],
    })
    payload = ConsoleDiagnostics(tmp_path).snapshot(
        LatestMessageCache(),
        lambda: {
            "phase": "ready", "actor_version": 8,
            "online_update": {"before": {"huge": list(range(1000))}},
            "runtime_profile": {"control_hz": 30},
        },
    )
    assert "consumed_uuids" not in payload["learning"]["cycle"]
    assert "attempted_uuids" not in payload["learning"]["cycle"]
    assert len(payload["learning"]["cycle"]["new_uuids"]) == 5
    assert "before" not in payload["learning"]["operation"]
    assert len(payload["learning"]["operation"]["reasons"]) == 20
    assert "online_update" not in payload["session"]
    assert payload["session"]["actor_version"] == 8

def test_metrics_follow_release_lineage_dedupe_steps_and_include_actor_objectives(tmp_path):
    parent_run=tmp_path/'learning/parent-run'; child_run=tmp_path/'learning/child-run'
    parent=tmp_path/'learning/rtc-v5/releases/parent.json'; child=tmp_path/'learning/rtc-v5/releases/child.json'
    _write(tmp_path/'learning/rtc-v5/current.json',{'release':str(child)})
    _write(parent,{'name':'parent','global_step':100,'offline_audit':str(parent_run/'release_audit.json')})
    _write(child,{'name':'child','global_step':200,'parent_release':str(parent),'offline_audit':str(child_run/'release_audit.json')})
    for run in (parent_run,child_run): run.mkdir(parents=True); _write(run/'release_audit.json',{})
    (parent_run/'metrics.jsonl').write_text('\n'.join([
        json.dumps({'global_step':50,'critic_loss':.3,'did_actor_update':0}),
        json.dumps({'global_step':100,'critic_loss':.2,'did_actor_update':1,'weighted_bc':.5,'weighted_q':-.02,'weighted_delta':.03,'delta_penalty':.0001}),
    ]))
    (child_run/'metrics.jsonl').write_text('\n'.join([
        json.dumps({'global_step':100,'critic_loss':.19,'did_actor_update':1,'weighted_bc':.4}),
        json.dumps({'global_step':200,'critic_loss':.1,'did_actor_update':0}),
    ]))
    payload=ConsoleDiagnostics(tmp_path,max_metrics=20).snapshot(LatestMessageCache(),lambda:None)
    rows=payload['learning']['metrics']
    assert [row['global_step'] for row in rows]==[50.0,100.0,200.0]
    assert rows[1]['critic_loss']==.19
    assert rows[1]['weighted_bc']==.4
    assert payload['learning']['history']['release_count']==2

def test_optional_learning_telemetry_and_progress_are_bounded(tmp_path):
    telemetry=tmp_path/'learning/telemetry'; telemetry.mkdir(parents=True)
    _write(telemetry/'episode_q.json',{'groups':[{'name':'autonomous_success','median':[.1,.3],'q25':[.05,.2],'q75':[.2,.4],'count':4}],'auc':.7})
    (telemetry/'decision_q.jsonl').write_text('\n'.join(json.dumps({'decision':i,'actor_q':i/10,'private':'x'}) for i in range(300)))
    (telemetry/'actor_delta.jsonl').write_text(json.dumps({'decision':9,'horizon_rms':[.1]*10,'joint_rms':[.2]*6}))
    _write(telemetry/'progress.json',{'phase':'critic_burnin','completed':250,'total':1000,'message':'critic warmup'})
    payload=ConsoleDiagnostics(tmp_path,max_metrics=240).snapshot(LatestMessageCache(),lambda:None)
    learning=payload['learning']
    assert learning['episode_q']['auc']==.7
    assert len(learning['decision_q'])==240 and learning['decision_q'][0]['decision']==60.0
    assert learning['actor_delta'][0]['horizon_rms']==[.1]*10
    assert learning['progress']=={'phase':'critic_burnin','completed':250,'total':1000,'fraction':.25,'message':'critic warmup'}


def _release_with_run(tmp_path, name, step, rows, **extra):
    run = tmp_path / f"learning/{name}-run"
    run.mkdir(parents=True)
    _write(run / "release_audit.json", {})
    (run / "metrics.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    release = tmp_path / f"learning/rtc-v5/releases/{name}.json"
    _write(release, {"name": name, "global_step": step, "actor_updates": step // 2,
                     "offline_audit": str(run / "release_audit.json"), **extra})
    return release, run


def test_rollback_history_follows_restored_release_not_replaced_chain(tmp_path):
    old, _ = _release_with_run(tmp_path, "old", 200, [{"global_step": s, "critic_loss": .1} for s in (100, 200)])
    bad, _ = _release_with_run(tmp_path, "bad", 400, [{"global_step": s, "critic_loss": .9} for s in (300, 400)],
                               parent_release=str(old))
    rollback, _ = _release_with_run(tmp_path, "rollback", 200, [], parent_release=str(bad), rollback_of=str(old))
    _write(tmp_path / "learning/rtc-v5/current.json", {"release": str(rollback)})
    learning = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)["learning"]
    assert [row["global_step"] for row in learning["metrics"]] == [100.0, 200.0]
    assert learning["history"]["release_count"] == 2
    assert learning["history"]["releases"] == [{"name": "rollback", "global_step": 200.0}]


def test_release_boundaries_are_reported_inside_plotted_range(tmp_path):
    first, _ = _release_with_run(tmp_path, "first", 200, [{"global_step": s, "critic_loss": .1} for s in (100, 200)])
    second, _ = _release_with_run(tmp_path, "second", 400, [{"global_step": s, "critic_loss": .1} for s in (300, 400)],
                                  parent_release=str(first))
    _write(tmp_path / "learning/rtc-v5/current.json", {"release": str(second)})
    history = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)["learning"]["history"]
    assert history["releases"] == [{"name": "first", "global_step": 200.0}, {"name": "second", "global_step": 400.0}]


def test_episode_q_prefers_deployed_release_evaluation_and_labels_source(tmp_path):
    release, run = _release_with_run(tmp_path, "deployed", 300, [{"global_step": 300, "critic_loss": .1}])
    _write(tmp_path / "learning/rtc-v5/current.json", {"release": str(release)})
    _write(tmp_path / "learning/telemetry/episode_q.json", {"groups": [], "auc": .4})
    learning = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)["learning"]
    assert learning["episode_q"] == {"groups": [], "auc": .4, "source": "latest_candidate"}
    _write(run / "episode_q_300.json", {"groups": [], "auc": .7})
    learning = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)["learning"]
    assert learning["episode_q"] == {"groups": [], "auc": .7, "source": "release", "source_step": 300}


def test_telemetry_reads_only_the_tail_of_append_only_logs(tmp_path, monkeypatch):
    import cobot_console.diagnostics as module
    monkeypatch.setattr(module, "_TELEMETRY_TAIL_BYTES", 200)
    lines = [json.dumps({"decision": index, "actor_q": .5, "actor_version": 1}) for index in range(100)]
    (tmp_path / "learning/telemetry").mkdir(parents=True)
    (tmp_path / "learning/telemetry/decision_q.jsonl").write_text("\n".join(lines), encoding="utf-8")
    rows = ConsoleDiagnostics(tmp_path).snapshot(LatestMessageCache(), lambda: None)["learning"]["decision_q"]
    assert 0 < len(rows) < 10
    assert rows[-1]["decision"] == 99.0
    assert all(row["decision"] > 90 for row in rows)
