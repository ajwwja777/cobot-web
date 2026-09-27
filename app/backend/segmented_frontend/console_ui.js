"use strict";

(function expose(root) {
  async function parseApiResponse(response) {
    const contentType = String(response.headers && response.headers.get
      ? response.headers.get("content-type") || ""
      : "").toLowerCase();
    const text = await response.text();
    let body = {};
    if (text) {
      if (!contentType.includes("application/json")) {
        throw new Error(
          "HTTP " + response.status + " 返回 " + (contentType || "未知类型") + "，预期 JSON；服务可能正在重启或端口转发已失效。",
        );
      }
      try {
        body = JSON.parse(text);
      } catch (_error) {
        throw new Error("HTTP " + response.status + " 返回了无效 JSON。");
      }
    }
    if (!response.ok) {
      const detail = body && (body.detail || body.error || body.error_code || body.message);
      throw new Error(detail ? (typeof detail === "string" ? detail : JSON.stringify(detail)) : "HTTP " + response.status);
    }
    return body;
  }

  function rltButtons(consoleStatus, session) {
    const result = {
      arm: false, start: false, pause: false, resume: false,
      success: false, failure: false, abort: false, next: false, stop: false,
      prepare: false, marker: false, save: false,
    };
    if (!consoleStatus || consoleStatus.selected_mode !== "rlt") return result;
    const backendPhase = String(consoleStatus.rlt_backend_phase || "offline");
    const backendUsable = ["ready_disarmed", "ready_armed", "armed", "running", "ready"].includes(backendPhase);
    if (!backendUsable || !session) return result;
    const phase = String(session.phase || "");
    const paused = Boolean(session.policy_paused);
    const active = ["rollout", "hil", "paused", "terminal_pending"].includes(phase);
    const terminal = ["waiting_scene"].includes(phase);
    const hil = phase === "hil" || (Array.isArray(session.expert_mask) && session.expert_mask.some(Boolean));
    const update = session.online_update || {};
    const learningBusy = ["preparing", "training"].includes(String(update.phase || ""));
    const ros = consoleStatus.ros_readiness;
    const robotReady = !ros || ros.status === 'ok' || ros.ready === true;
    result.prepare = ["disarmed", "stopped"].includes(phase) && robotReady;
    result.start = ["disarmed", "stopped", "armed", "ready", "waiting_scene"].includes(phase) && paused && robotReady && !learningBusy && !session.fault_reason;
    result.pause = active && !paused;
    result.resume = active && paused && !hil && robotReady && !learningBusy && !session.outcome && !session.fault_reason;
    result.success = result.failure = result.abort = result.save = active && !hil && !session.fault_reason;
    result.marker = active && !session.fault_reason;
    result.stop = !["disarmed", "stopped", "finalizing", "replay_committing"].includes(phase);
    return result;
  }

  function rltGuidance(consoleStatus, session) {
    const status = consoleStatus || {};
    const backendPhase = String(status.rlt_backend_phase || "offline");
    const errorCode = status.rlt_lifecycle && status.rlt_lifecycle.error_code;
    if (status.rlt_model && !status.rlt_model.validated) return "新三视角模型尚未验证与部署；普通采集可使用。";
    if (backendPhase === "offline") return "RLT 后端未启动。普通采集仍可使用；需要 RLT 时先在终端运行 rlt_up。";
    if (backendPhase === "loading_env") return "模型已加载，等待机械臂与三相机就绪。";
    if (backendPhase === "loading" || backendPhase.startsWith("loading_")) return "RLT 模型正在加载，请等待后端状态变为就绪。";
    if (backendPhase === "stopping") return "RLT 后端正在安全停止。";
    if (backendPhase === "fault") return "RLT 后端故障：" + (errorCode || "unknown_fault");
    if (!session && status.rlt_lifecycle && status.rlt_lifecycle.children && status.rlt_lifecycle.children.model && !status.rlt_lifecycle.children.session)
      return "模型已加载，等待采集服务就绪。";
    if (!session) return "正在连接内部 RLT 后端…";
    if (session.fault_reason) return "RLT Session 故障：" + session.fault_reason;
    if (session.online_update && ["preparing", "training"].includes(session.online_update.phase))
      return "正在准备数据/更新 RL 模块；完成后下一轮采用通过验证的新 actor。";
    if (session.phase === "hil") return "正在人工示教，数据会记录；释放示教按钮后保持暂停，再标记结果或继续。";
    if (status.ros_readiness && status.ros_readiness.status !== 'ok' && !status.ros_readiness.ready)
      return "机械臂/相机节点未就绪：" + String(status.ros_readiness.error_code || "等待实时数据");
    if (["disarmed", "stopped", "ready"].includes(session.phase)) return "模型已就绪，可以开始推理。";
    if (["rollout", "hil", "paused", "terminal_pending"].includes(session.phase) && !session.policy_paused) return "策略正在运行；需要判定结果时请先暂停。";
    if (["rollout", "hil", "paused", "terminal_pending"].includes(session.phase) && session.policy_paused) return "策略已暂停，可继续、标记结果或放弃本轮。";
    if (session.phase === "waiting_scene") return "本轮已结束；确认场景复位后开始下一轮。";
    return "RLT 后端已连接，状态：" + String(session.phase || backendPhase);
  }

  function rltShortcutAction(buttons, key) {
    const state = buttons || {};
    if (key === "ArrowUp") return state.success ? "success" : null;
    if (key === "ArrowDown") return state.failure ? "failure" : null;
    if (key === "ArrowLeft") return state.abort ? "abort" : null;
    if (key !== "ArrowRight") return null;
    if (state.start) return "start";
    if (state.pause) return "pause";
    if (state.resume) return "resume";
    if (state.next) return "next";
    return null;
  }

  // Actor release picker: label shows what the release is, since several share an actor_updates number.
  function releaseOptionLabel(item) {
    const value = item || {};
    return (value.current ? "● " : "") + String(value.kind || "release") + " · actor " + String(value.actor_updates == null ? "?" : value.actor_updates)
      + " · " + String(value.name || "").replace(/\.json$/, "");
  }
  // Switching restarts the Session, so it is only offered between episodes (mirrors release_select.SWITCH_PHASES).
  function releaseSwitchAllowed(consoleStatus, session, listing, selected, busy) {
    if (busy || !consoleStatus || consoleStatus.selected_mode !== "rlt" || !listing || !selected) return false;
    if (selected === listing.current) return false;
    const phase = String((session && session.phase) || "offline");
    return (listing.switch_phases || []).includes(phase);
  }

  const api = {
    parseApiResponse, rltButtons, rltGuidance, rltShortcutAction,
    releaseOptionLabel, releaseSwitchAllowed,
  };
  root.CobotConsoleUI = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
