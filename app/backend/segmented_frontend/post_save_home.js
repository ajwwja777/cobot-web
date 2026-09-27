"use strict";

(function expose(root) {
  function choosePose(poses, preferred) {
    const values = (Array.isArray(poses) ? poses : []).map(String);
    if (preferred && values.includes(String(preferred))) return String(preferred);
    for (const candidate of ["plug2", "plug", "origin"]) {
      if (values.includes(candidate)) return candidate;
    }
    return values[0] || "";
  }

  function shortcutAction(key, captureState, resetEnabled) {
    const state = String(captureState || "");
    if (key === "ArrowLeft" && ["recording", "paused"].includes(state)) return "discard";
    if (key !== "ArrowRight") return null;
    if (["idle", "committed"].includes(state)) return "start";
    if (["recording", "paused"].includes(state)) return resetEnabled ? "stop_home" : "stop";
    return null;
  }

  async function run(options) {
    const target = String(options.target || "");
    const pose = String(options.pose || "");
    if (!target || !pose) throw new Error("请选择保存后的复位目标和 Pose");
    if (options.confirm && !options.confirm(target, pose)) return { cancelled: true };
    options.onPhase("saving", { target, pose });
    const saved = await options.stop();
    options.onPhase("saved", { target, pose, saved });
    if (options.prepareNext) await options.prepareNext();
    const job = await options.startHome(target, pose);
    if (!job) throw new Error("数据已保存，但 Home 未启动");
    options.onPhase("homing", { target, pose, saved, job });
    const completed = await options.waitHome(job);
    if (!completed || completed.phase !== "completed") {
      throw new Error("数据已保存，但复位未完成：" + String((completed || {}).log_tail || (completed || {}).phase || "unknown"));
    }
    options.onPhase("complete", { target, pose, saved, job, completed });
    return { saved, job, completed };
  }

  const api = { choosePose, shortcutAction, run };
  root.CobotPostSaveHome = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
