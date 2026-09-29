"""Validated RLT deployment catalog for the unified console."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

from .paths import RLT as PROJECT, PROJECT as PLATFORM, RUNTIME_ROOT, RLT_MODELS
V3_RUN = PROJECT / "outputs/rlt/plug_v3_yyshadow"
V3_MANIFEST = PROJECT / "configs/rlt/plug_v3_yyshadow/manifest.json"
V3_CONFIG = PROJECT / "configs/rlt/plug_v3_yyshadow/online_rl.yaml"
DEFAULT_STATE = RUNTIME_ROOT / "data-console/model-selection.json"

class ModelSelectionError(ValueError):
    pass

def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None

def _p(group: str, key: str, value: Any, unit: str = "") -> Dict[str, Any]:
    return {"group": group, "key": key, "value": value, "unit": unit}

class ModelCatalog:
    def __init__(self, profile: str, state_path: Path = DEFAULT_STATE) -> None:
        self.profile = str(profile)
        self.state_path = Path(state_path)

    def _parameters(self, manifest: Dict[str, Any]) -> list[Dict[str, Any]]:
        try: config = yaml.safe_load(V3_CONFIG.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError): config = {}
        rl = ((config.get("experiment") or {}).get("rl") or {})
        runtime = config.get("runtime") or {}
        actor, learner = runtime.get("actor_service") or {}, runtime.get("learner_service") or {}
        replay, env = runtime.get("replay") or {}, runtime.get("env_driver") or {}
        training, contract = manifest.get("training") or {}, manifest.get("model_contract") or {}
        validation = manifest.get("cobot_validation") or {}
        rows = [
            _p("Stage 1","checkpoint step",manifest.get("checkpoint_step")),
            _p("Stage 1","training steps",training.get("steps")),
            _p("Stage 1","seed",training.get("seed")),
            _p("Stage 1","global batch",training.get("global_batch_size")),
            _p("Stage 1","FSDP devices",training.get("fsdp_devices")),
            _p("Model","physical arm",contract.get("physical_arm")),
            _p("Model","state / action dim",f"{contract.get('physical_state_dim')} / {contract.get('physical_action_dim')}"),
            _p("Model","model action dim",contract.get("model_action_dim")),
            _p("Model","model horizon",contract.get("model_horizon")),
            _p("Model","RL token z dim",contract.get("z_rl_dim")),
            _p("Model","cameras",", ".join(contract.get("cameras") or [])),
            _p("Online RL","chunk length",rl.get("chunk_len")),
            _p("Online RL","gamma",rl.get("gamma")),
            _p("Online RL","exploration std",rl.get("fixed_std")),
            _p("Online RL","reference dropout",rl.get("reference_dropout_prob")),
            _p("Online RL","delta weight",rl.get("delta_weight")),
            _p("Online RL","warmup BC / Q",f"{rl.get('warmup_bc_weight')} / {rl.get('warmup_q_weight')}"),
            _p("Online RL","online BC / Q",f"{rl.get('online_bc_weight')} / {rl.get('online_q_weight')}"),
            _p("Networks","actor",f"{rl.get('actor_num_layers')} x {rl.get('actor_hidden_dim')}"),
            _p("Networks","critic",f"{rl.get('critic_num_layers')} x {rl.get('critic_hidden_dim')}"),
            _p("Networks","actor / critic lr",f"{rl.get('actor_lr')} / {rl.get('critic_lr')}"),
            _p("Networks","target tau",rl.get("target_tau")),
            _p("Networks","actor period",rl.get("actor_update_period"),"critic steps"),
            _p("Warmup","minimum replay",rl.get("warmup_min_size"),"transitions"),
            _p("Warmup","training budget",rl.get("warmup_post_collect_updates"),"updates"),
            _p("Online","batch size",learner.get("sample_batch_size")),
            _p("Online","updates / cycle",rl.get("grad_updates_per_cycle")),
            _p("Online","publish interval",learner.get("push_actor_interval_steps"),"steps"),
            _p("Online","checkpoint interval",learner.get("checkpoint_interval_steps"),"steps"),
            _p("Replay","capacity",replay.get("capacity"),"transitions"),
            _p("Runtime","control rate",env.get("control_frequency_hz"),"Hz"),
            _p("Runtime","executed horizon",env.get("chunk_exec_horizon")),
            _p("Runtime","actor pull interval",actor.get("pull_params_interval_sec"),"s"),
            _p("Runtime","safe fallback",env.get("safe_fallback_to_ref")),
            _p("Validation","steady inference",(validation.get("inference_ms") or [None])[-1],"ms"),
        ]
        return [x for x in rows if x["value"] is not None and x["value"] != "None / None"]

    def _models(self) -> list[Dict[str, Any]]:
        if self.profile != "plug_v3_yyshadow": return []
        manifest = _json(V3_MANIFEST) or {}
        checkpoint = Path(str(manifest.get("checkpoint","")))
        valid = manifest.get("cohort") == self.profile and manifest.get("status") == "offline_validated" and (checkpoint/"params").is_dir()
        learner = _json(V3_RUN/"online/metrics/learner_status.json") or {}
        snapshot = RLT_MODELS/"online/actor_snapshot/actor_snapshot.pkl"
        from .rlt_progress import published_actor
        publication = published_actor(snapshot)
        version = publication.get("published_actor_version")
        progress = {**publication, "learner_step": learner.get("global_step"),
                    "learner_actor_version": learner.get("actor_version"),
                    "learner_observed_at": learner.get("timestamp"),
                    "publication_tracked": True}
        params = self._parameters(manifest)
        return [
          {"id":"plug_v3-stage1-reference","label":"plug_v3 / Stage-1 reference","cohort":self.profile,
           "kind":"reference","available":bool(valid),"status":manifest.get("status","missing"),
           "checkpoint":manifest.get("checkpoint"),"checkpoint_step":manifest.get("checkpoint_step"),
           "run_root":str(V3_RUN),"start_target":"reference",
           "description":"Frozen Stage-1 policy; online actor is bypassed. Baseline comparison and warmup collection.",
           "parameters":params},
          {"id":"plug_v3-frozen-latest","label":"plug_v3 / frozen latest actor","cohort":self.profile,
           "kind":"frozen","available":bool(valid and snapshot.is_file() and learner.get("ready_for_online")),
           "status":"ready" if snapshot.is_file() and learner.get("ready_for_online") else "awaiting_warmup",
           "checkpoint":str(snapshot),"checkpoint_step":version,"actor_version":version,"step":publication.get("published_learner_step"), **progress,
           "run_root":str(V3_RUN),"start_target":"frozen",
           "description":"Same latest actor snapshot with actor updates frozen for controlled comparison.",
           "parameters":params},
          {"id":"plug_v3-online-latest","label":"plug_v3 / latest online actor","cohort":self.profile,
           "kind":"online","available":bool(valid and snapshot.is_file() and learner.get("ready_for_online")),
           "status":"ready" if snapshot.is_file() and learner.get("ready_for_online") else "awaiting_warmup",
           "checkpoint":str(snapshot),"checkpoint_step":version,"actor_version":version,"step":publication.get("published_learner_step"), **progress,
           "run_root":str(V3_RUN),"start_target":"online",
           "description":"Latest actor/critic after the warmup gate; online updates remain enabled.",
           "parameters":params}
        ]

    def listing(self) -> Dict[str, Any]:
        models = self._models()
        requested = (_json(self.state_path) or {}).get("model_id")
        available = {m["id"] for m in models if m["available"]}
        current = requested if requested in available else next((m["id"] for m in models if m["available"]),None)
        for model in models: model["current"] = model["id"] == current
        return {"profile":self.profile,"current":current,"models":models}

    def current(self) -> Optional[Dict[str, Any]]:
        value = self.listing()
        return next((x for x in value["models"] if x["id"] == value["current"]),None)

    def select(self, model_id: str) -> Dict[str, Any]:
        value = self.listing()
        model = next((x for x in value["models"] if x["id"] == model_id),None)
        if model is None: raise ModelSelectionError("model_not_registered_for_profile")
        if not model["available"]: raise ModelSelectionError("model_not_ready: "+str(model["status"]))
        self.state_path.parent.mkdir(parents=True,exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"model_id":model_id},indent=2)+"\n",encoding="utf-8")
        os.replace(temporary,self.state_path)
        return self.listing()
