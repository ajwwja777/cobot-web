"use strict";

(function exposeWorkflow(root) {
  const ERROR_MESSAGES = Object.freeze({
    latest_episode_labels_required: "请先为上一条 episode 选择成功或失败。",
    recorder_not_ready: "记录器尚未就绪，请确认 Task2、ROS 和三路相机均已启动。",
    recorder_already_started: "记录器已经在工作，请勿重复开始。",
    no_active_recording: "当前没有正在记录的 rollout。",
    episode_already_exists: "目标 episode 已存在，请刷新目录编号后重试。",
    invalid_storage_request: "保存目录或实验标识无效，请检查后重试。",
    storage_prepare_failed: "无法准备保存目录，请检查权限和剩余空间。",
    preview_active: "回放正在生成，暂时不能删除这条 episode。",
    preview_not_ready: "回放还没有生成完成。",
    preview_queue_full: "回放任务较多，请稍后重试。",
    episode_delete_refused: "删除被安全检查拒绝，请刷新 episode 后重试。",
  });

  function formatApiError(detail) {
    if (typeof detail === "string") return ERROR_MESSAGES[detail] || detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (!item || typeof item !== "object") return String(item);
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "请求";
          return `${location}: ${item.msg || "输入无效"}`;
        })
        .join("；");
    }
    if (detail && typeof detail === "object") {
      if (typeof detail.msg === "string") return detail.msg;
      try {
        return JSON.stringify(detail);
      } catch (_error) {
        return "请求失败，请查看服务日志。";
      }
    }
    return "请求失败，请查看服务日志。";
  }

  function outcomeRequest(episodeUuid, outcome) {
    if (!["success", "failure"].includes(outcome)) {
      throw new TypeError("outcome must be success or failure");
    }
    return { episode_uuid: String(episodeUuid), outcome };
  }

  function deleteRequest(episodeUuid) {
    return { episode_uuid: String(episodeUuid) };
  }

  function previewProgress(status) {
    if (!status || typeof status !== "object") return "回放状态未知";
    if (status.state === "queued") return "回放已排队，等待生成";
    if (status.state === "generating") {
      return `正在生成回放：${Number(status.completed_frames || 0)} / ${Number(status.total_frames || 0)}`;
    }
    if (status.state === "ready") return "回放已就绪";
    if (status.state === "error") return "回放生成失败，请查看服务日志";
    return "尚未生成回放";
  }

  function stopProgress(status) {
    const state = status && typeof status === "object" ? status.state : "unknown";
    const frames = Number(status && status.frames_written) || 0;
    if (state === "error" || state === "fatal") {
      return {
        complete: false,
        failed: true,
        message: "HDF5 保存失败，请查看记录器状态。",
      };
    }
    if (state === "stopped") {
      const filename = status.episode_file || "episode HDF5";
      return {
        complete: true,
        failed: false,
        message: `${filename} 已完整保存，共 ${frames} frames。`,
      };
    }
    return {
      complete: false,
      failed: false,
      message: `采样已停止，正在原子保存 ${frames} frames…`,
    };
  }

  function episodeOutcomeText(episode) {
    if (episode && episode.episode_outcome === "success") return "成功";
    if (episode && episode.episode_outcome === "failure") return "失败";
    return "待标注";
  }

  function phaseText(phase) {
    const start = Number(phase.start_frame);
    const end = Number(phase.end_frame);
    const frames = start === end ? `frame ${start}` : `frame ${start}–${end}`;
    const sides = Array.isArray(phase.human_sides) ? phase.human_sides : [];
    let actor = "人工接管";
    if (sides.includes("left") && sides.includes("right")) actor = "双臂人工接管";
    else if (sides.includes("left")) actor = "左臂人工接管";
    else if (sides.includes("right")) actor = "右臂人工接管";
    return `${frames}：${actor}`;
  }

  function seriesRequest(values) {
    return Object.fromEntries(
      ["data_root", "task_id", "model_id", "checkpoint_id", "dataset_round"].map(
        (name) => [name, String(values[name] || "").trim()],
      ),
    );
  }

  const api = {
    deleteRequest,
    episodeOutcomeText,
    formatApiError,
    outcomeRequest,
    phaseText,
    previewProgress,
    seriesRequest,
    stopProgress,
  };
  root.Task5Workflow = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
