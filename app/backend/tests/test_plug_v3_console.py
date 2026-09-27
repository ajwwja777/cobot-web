import json
from pathlib import Path

import yaml

from cobot_console import model_catalog
from cobot_console.diagnostics import ConsoleDiagnostics
from cobot_console.device_control import DeviceController
from tests.test_console_api import _fresh_cache


def test_model_catalog_exposes_validated_stage1_and_core_parameters(tmp_path, monkeypatch):
    run=tmp_path/"plug_v3_yyshadow"; (run/"online/metrics").mkdir(parents=True)
    checkpoint=tmp_path/"checkpoint"; (checkpoint/"params").mkdir(parents=True)
    manifest=tmp_path/"manifest.json"
    manifest.write_text(json.dumps({
        "cohort":"plug_v3_yyshadow","status":"offline_validated","checkpoint":str(checkpoint),
        "checkpoint_step":4999,"training":{"steps":5000,"seed":42,"global_batch_size":32,"fsdp_devices":4},
        "model_contract":{"physical_arm":"right","physical_state_dim":7,"physical_action_dim":7,
        "model_action_dim":32,"model_horizon":50,"z_rl_dim":2048,"cameras":["mid","left_wrist","right_wrist"]},
        "cobot_validation":{"inference_ms":[77.0]},
    }))
    config=tmp_path/"online.yaml"
    config.write_text(yaml.safe_dump({"experiment":{"rl":{"chunk_len":10,"gamma":.99,"fixed_std":.002,
        "reference_dropout_prob":.5,"delta_weight":10,"warmup_bc_weight":10,"warmup_q_weight":.1,
        "online_bc_weight":5,"online_q_weight":.1,"actor_hidden_dim":256,"actor_num_layers":2,
        "critic_hidden_dim":256,"critic_num_layers":2,"actor_lr":1e-4,"critic_lr":1e-4,
        "target_tau":.005,"actor_update_period":2,"warmup_min_size":600,
        "warmup_post_collect_updates":20000,"grad_updates_per_cycle":5}},
        "runtime":{"actor_service":{"pull_params_interval_sec":.25},"learner_service":{"sample_batch_size":128,
        "push_actor_interval_steps":500,"checkpoint_interval_steps":1000},"replay":{"capacity":200000},
        "env_driver":{"control_frequency_hz":20,"chunk_exec_horizon":10,"safe_fallback_to_ref":True}}}))
    monkeypatch.setattr(model_catalog,"V3_RUN",run)
    monkeypatch.setattr(model_catalog,"V3_MANIFEST",manifest)
    monkeypatch.setattr(model_catalog,"V3_CONFIG",config)
    catalog=model_catalog.ModelCatalog("plug_v3_yyshadow",tmp_path/"selection.json")
    listing=catalog.listing()
    assert listing["current"]=="plug_v3-stage1-reference"
    assert listing["models"][0]["available"] is True
    params={row["key"]:row["value"] for row in listing["models"][0]["parameters"]}
    assert params["gamma"]==.99 and params["minimum replay"]==600 and params["control rate"]==20
    assert [item["id"] for item in listing["models"]] == [
        "plug_v3-stage1-reference", "plug_v3-frozen-latest", "plug_v3-online-latest"
    ]
    assert listing["models"][1]["available"] is False
    assert listing["models"][2]["available"] is False


def test_v3_diagnostics_streams_real_learner_metrics(tmp_path):
    run=tmp_path/"plug_v3_yyshadow"; metrics=run/"online/metrics"; metrics.mkdir(parents=True)
    (metrics/"learner_metrics.jsonl").write_text(
        json.dumps({"global_step":17,"critic_loss":.4,"q1_mean":.2,"q2_mean":.1,
                    "target_q_mean":.3,"actor_loss":.5,"did_actor_update":1})+"\n")
    (metrics/"learner_status.json").write_text(json.dumps(
        {"global_step":17,"replay_size":640,"actor_version":0,"ready_for_online":False}))
    payload=ConsoleDiagnostics(run).snapshot(_fresh_cache(),lambda:None)
    assert payload["learning"]["metrics"][0]["critic_loss"]==.4
    assert payload["learning"]["progress"]["message"]=="Training warmup actor and critic"
    assert payload["learning"]["release"]["global_step"]==17


def test_v3_device_commands_are_fixed_allowlist(tmp_path):
    control=DeviceController(tmp_path)
    reference=control._normalize({"component":"rlt","action":"start","target":"reference",
                                  "model":"plug_v3-stage1-reference"})
    assert Path(control._command(reference)[-2]).name=="rlt_v3_up.sh" and control._command(reference)[-1]=="reference"
    warmup=control._normalize({"component":"rlt","action":"start","target":"warmup",
                               "model":"plug_v3-stage1-reference"})
    assert Path(control._command(warmup)[-2]).name=="rlt_v3_up.sh" and control._command(warmup)[-1]=="warmup"
    frozen=control._normalize({"component":"rlt","action":"start","target":"frozen",
                               "model":"plug_v3-frozen-latest"})
    assert Path(control._command(frozen)[-2]).name=="rlt_v3_up.sh" and control._command(frozen)[-1]=="frozen"
    online=control._normalize({"component":"rlt","action":"start","target":"online",
                               "model":"plug_v3-online-latest"})
    assert Path(control._command(online)[-2]).name=="rlt_v3_up.sh" and control._command(online)[-1]=="online"
    down=control._normalize({"component":"rlt","action":"down","model":"plug_v3-stage1-reference"})
    assert Path(control._command(down)[-1]).name=="rlt_v3_down.sh"


def test_reference_mode_bypasses_the_warmup_to_online_gate():
    script = (
        Path(__file__).resolve().parents[3] / "scripts" / "rlt_v3_up.sh"
    ).read_text()
    reference = script.split('if [[ "$MODE" == reference ]]', 1)[1].split(
        "else", 1
    )[0]
    assert "export COBOT_RLT_DISABLE_PHASE_CONTROLLER=1" in reference
    assert "export RLT_DISABLE_LEARNER=1" in reference


def test_frontend_contains_model_selector_and_parameter_panel():
    root=Path(__file__).resolve().parents[1]/"segmented_frontend"
    html=(root/"index.html").read_text()
    app=(root/"app.js").read_text()
    assert 'id="rlt-model-select"' in html and 'id="rlt-core-parameters"' in html
    assert 'id="device-rlt-warmup"' in html
    assert "/api/rlt/models" in app and "/api/rlt/model" in app
