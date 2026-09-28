import json
from cobot_console import model_metadata as metadata

def test_rlt_backup_name_is_not_its_training_step(tmp_path):
    path=tmp_path/"rlt/plug_insertion/history/pre-experts120-5k/online/actor_snapshot/actor_snapshot.pkl"
    path.parent.mkdir(parents=True)
    (path.parent.parent/"release.json").write_text(json.dumps({"global_step":20000,"actor_version":10000}))
    row=metadata.describe({"kind":"unadapted","checkpoint":str(path),"available":False})
    assert row["step"]==20000 and row["actor_version"]==10000
    assert row["family"]=="RLT" and row["task"]=="plug_insertion"

def test_base_is_not_deployable_and_unknown_steps_are_not_invented():
    row=metadata.describe({"checkpoint":"/model/vla-platform/lingbot_v2/base/Qwen3","available":False})
    assert row["availability"]=="base_model"
    assert row.get("step") is None

def test_dagger_keeps_parent_and_round_steps():
    row=metadata.describe({"id":"pi05-in-the-pot-dagger","kind":"pi05","step":3000,
        "checkpoint":"/model/vla-platform/pi05/in_the_pot/dagger_2000plus3000"})
    assert row["parent_step"]==2000 and row["step"]==3000


def test_migrated_cli_and_missing_assets_are_separate_statuses(tmp_path, monkeypatch):
    registry_source = (metadata.VLA / "integrations/cobot/registry.py").read_text()
    monkeypatch.setattr(metadata, "VLA", tmp_path)
    (tmp_path/"configs").mkdir()
    entry=tmp_path/"integrations/cobot/test"
    entry.mkdir(parents=True)
    (entry.parent / "registry.py").write_text(registry_source)
    (entry/"interface_live.sh").write_text("#!/bin/sh")
    checkpoint=tmp_path/"checkpoint";checkpoint.mkdir()
    row={"id":"legacy","family":"Legacy","task":"in_the_pot","step":4000,
        "root":"integrations/cobot/test","checkpoint":str(checkpoint),"required":[],
        "runtime_dependencies":[],"managed":False,"args":["interface_live.sh","4000"],
        "home_pose":"origin","control_hz":20}
    (tmp_path/"configs/cobot_models.json").write_text(json.dumps({"models":[row]}))
    model=metadata.vla_models()[0]
    assert model["cli_available"] and not model["available"]
    assert model["availability"]=="cli_only"
    checkpoint.rmdir()
    assert metadata.vla_models()[0]["availability"]=="missing_files"


def test_vla_status_requires_this_process_readiness_and_explicit_resume(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    from cobot_console import deployment
    runtime=deployment.ManagedRuntime(tmp_path)
    deployment.atomic_json(runtime.registry, {"model":{"id":"g05","kind":"vla"},
        "pid":123,"started_at":time.time(),"log_path":str(tmp_path/"log")})
    monkeypatch.setattr(runtime, "_alive", lambda state: True)
    deployment.atomic_json(tmp_path/"vla-gate.json", {"ready":True,"paused":False,"pid":456})
    assert runtime.status()["phase"]=="loading"
    deployment.atomic_json(tmp_path/"vla-gate.json", {"ready":True,"paused":True,"pid":123})
    assert runtime.status()["phase"]=="paused"
    calls=[]
    monkeypatch.setattr(deployment.subprocess,"run",lambda args,**kwargs: calls.append(args) or SimpleNamespace(returncode=0))
    runtime.action("resume")
    assert calls[-1][-1]=="--arm"
    assert '"$@"' in calls[-1][2]
    runtime.action("pause")
    assert calls[-1][-1]=="pause"
