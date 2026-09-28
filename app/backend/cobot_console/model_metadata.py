"""Human-readable model identity and preserved VLA entrypoints (no model imports)."""
import json
import re
import shlex
from pathlib import Path
from .paths import PROJECT

VLA = PROJECT.parent / "vla-platform"

def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}

def vla_models():
    result = []
    for row in read(VLA / "configs/cobot_models.json").get("models", []):
        entry = VLA / row["root"]
        missing = [p for p in [row["checkpoint"], *row["required"], *row["runtime_dependencies"],
                    str(entry / row["args"][0])] if not Path(p).exists()]
        managed = row["managed"]
        cli_args = (["interface_task2_teach_rtc_live.sh", str(row["step"])]
                    if managed else row["args"])
        cli = "cd " + shlex.quote(str(entry)) + "\n./" + " ".join(shlex.quote(x) for x in cli_args)
        reason = ("缺少文件：" + ", ".join(missing)) if missing else (
            "" if managed else "终端入口已迁入；原脚本启动即运动，尚未接入网页暂停控制")
        reason_en = ("Missing files: " + ", ".join(missing)) if missing else (
            "" if managed else "CLI migrated; direct-motion entry requires web pause control")
        result.append(dict(id=row["id"], family=row["family"], task=row["task"], step=row["step"],
            parent_step=row.get("parent_step"), checkpoint=row["checkpoint"],
            label=row["family"] + " · " + row["task"] + " · " + str(row["step"]),
            kind="vla", mode="evaluation", home_pose=row["home_pose"], control_hz=row["control_hz"],
            available=managed and not missing, cli_available=not missing, cli_command=cli,
            unavailable_reason=reason, unavailable_reason_en=reason_en,
            availability="missing_files" if missing else "ready" if managed else "cli_only",
            entry=str(VLA / "integrations/cobot/managed_model.py"),
            validation="Preserved deployment entry; hardware evaluation pending",
            training_enabled=False))
    return result

def describe(model):
    result = dict(model)
    path = Path(model.get("checkpoint") or "")
    parts = path.parts
    family = model.get("family")
    if not family:
        family = "RLT" if model.get("kind") == "rlt" or "rlt" in parts else "π0.5" if model.get("kind") == "pi05" else ""
        if not family and "vla-platform" in parts:
            family = parts[parts.index("vla-platform") + 1]
    task = model.get("task") or next((p for p in parts if p in {
        "in_the_pot", "plug_insertion", "lift_book", "put_two_fruits", "base"}), "unknown")
    if model.get("kind") == "rlt":
        task = model.get("task") or "plug_insertion"
    elif model.get("kind") == "pi05" and task in {"base", "unknown"}:
        task = "in_the_pot"
    step = model.get("step")
    if family == "RLT":
        result["base_step"] = 4999 if task == "plug_insertion" else None
        # Backup names describe the NEXT experiment, not this checkpoint's steps.
        run = path.parent.parent
        meta = read(run / "release.json") or read(run / "training_manifest.json")
        if path.parent.name == "online_resume_smoke":
            meta = read(path.parent / "report.json").get("final", {})
        if "history" in parts and step is None:
            result["step"] = meta.get("global_step")
            result["actor_version"] = meta.get("actor_version")
        if model.get("mode") == "reference":
            result["stage"] = "stage1"
        elif "warmup" in model.get("id", "") or "warmup_5000" in parts:
            result["stage"] = "warmup"
        elif model.get("mode") in {"online", "frozen"}:
            result["stage"] = model["mode"]
        else:
            result["stage"] = "history"
    elif step is None:
        for part in reversed(parts):
            match = re.fullmatch(r"(?:step_|baseline_)(\d+)", part)
            if match:
                result["step"] = int(match.group(1))
                break
    if model.get("id") == "pi05-in-the-pot-dagger":
        result["parent_step"] = 2000
    result.update(family=family or "unknown", task=task)
    if task == "base" and model.get("kind") not in {"rlt", "pi05", "vla"}:
        result.update(availability="base_model", available=False,
            unavailable_reason="基础模型依赖，不是场景部署权重",
            unavailable_reason_en="Base model dependency; not a trained task policy")
    result.setdefault("availability", "ready" if model.get("available") else "unregistered")
    return result
