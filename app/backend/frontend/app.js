"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const {
  deleteRequest,
  episodeOutcomeText,
  formatApiError,
  outcomeRequest,
  phaseText,
  previewProgress,
  seriesRequest,
  stopProgress,
} = window.Task5Workflow;

const REVIEW_ONLY = window.location && window.location.pathname.startsWith("/rlt-review");
const apiUrl = path => REVIEW_ONLY && path.startsWith("/api/") ? "/api/rlt-recorder" + path : path;

const state = {
  prepared: null,
  recorderActive: false,
  episodes: [],
  selectedEpisodeUuid: null,
  previewGeneration: 0,
  episodeGeneration: 0,
};

function message(text, failed = false) {
  const node = $("#operation-message");
  node.textContent = text;
  node.classList.toggle("error", failed);
}

async function request(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (window.CobotConsoleUI) return window.CobotConsoleUI.parseApiResponse(response);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(formatApiError(body.detail || `HTTP ${response.status}`));
  }
  return body;
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function currentSeries() {
  return seriesRequest(formObject($("#series-form")));
}

function queryRoot() {
  const root =
    (state.prepared && state.prepared.data_root) || currentSeries().data_root;
  return `data_root=${encodeURIComponent(root)}`;
}

function saveSeriesLocally(series) {
  try {
    localStorage.setItem("task5-v1-series", JSON.stringify(series));
  } catch (_error) {
    // The backend remains authoritative when browser storage is unavailable.
  }
}

function restoreSeriesLocally() {
  try {
    const saved = JSON.parse(localStorage.getItem("task5-v1-series") || "null");
    if (!saved || typeof saved !== "object") return;
    const form = $("#series-form");
    for (const name of [
      "data_root",
      "task_id",
      "model_id",
      "checkpoint_id",
      "dataset_round",
    ]) {
      if (typeof saved[name] === "string" && saved[name]) {
        form.elements[name].value = saved[name];
      }
    }
  } catch (_error) {
    // Ignore malformed or unavailable browser storage.
  }
}

function selectedEpisode() {
  return state.episodes.find(
    (item) => item.episode_uuid === state.selectedEpisodeUuid,
  );
}

function updateButtons() {
  const blocked = Boolean(state.prepared && state.prepared.label_blocked);
  $("#start-button").disabled =
    REVIEW_ONLY || state.recorderActive || !state.prepared || blocked;
  $("#stop-button").disabled = REVIEW_ONLY || !state.recorderActive;
  const noEpisode = !state.selectedEpisodeUuid || state.recorderActive;
  $("#success-button").disabled = noEpisode;
  $("#failure-button").disabled = noEpisode;
  $("#replay-episode").disabled = noEpisode;
  $("#delete-episode").disabled = noEpisode;
}

async function refreshStatus() {
  try {
    const status = await request("/api/status");
    state.recorderActive = Boolean(status.active);
    $("#recorder-state").textContent = status.state;
    $("#handover-mode").textContent = status.handover_mode;
    $("#left-source").textContent = status.control_source_left;
    $("#right-source").textContent = status.control_source_right;
    $("#frame-count").textContent = `${status.frames_written} / ${status.frames_sampled}`;
    $("#ros-state").textContent = status.ros_error_code || status.ros_state;
    updateButtons();
  } catch (error) {
    state.recorderActive = false;
    $("#recorder-state").textContent = "offline";
    updateButtons();
    message(error.message, true);
  }
}

function renderPrepared(prepared) {
  state.prepared = prepared;
  $("#episode-directory").textContent = prepared.episode_directory;
  $("#next-episode").textContent = `episode_${String(prepared.next_episode_index).padStart(6, "0")}.hdf5`;
  const storageState = $("#storage-state");
  if (prepared.label_blocked) {
    storageState.textContent = "上一条待选择结果";
    storageState.classList.add("warning");
  } else {
    storageState.textContent = "目录可用";
    storageState.classList.remove("warning");
  }
  updateButtons();
}

async function prepareStorage({ quiet = false, previewLatest = false } = {}) {
  const series = currentSeries();
  const prepared = await request("/api/storage/prepare", {
    method: "POST",
    body: JSON.stringify(series),
  });
  saveSeriesLocally({ ...series, data_root: prepared.data_root });
  $("#series-form").elements.data_root.value = prepared.data_root;
  renderPrepared(prepared);
  await refreshEpisodes(previewLatest ? prepared.latest_episode_uuid : (state.selectedEpisodeUuid || prepared.latest_episode_uuid));
  if (previewLatest && prepared.latest_episode_uuid) {
    await requestPreview(prepared.latest_episode_uuid);
  }
  if (!quiet) {
    message(
      prepared.label_blocked
        ? "目录已读取；请先为上一条 episode 选择成功或失败。"
        : "保存目录已准备好，可以开始记录。",
      false,
    );
  }
  return prepared;
}

function renderInterventions(phases = []) {
  const root = $("#interventions");
  root.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = `人工接管阶段（${phases.length}）`;
  root.append(title);
  if (!phases.length) {
    const empty = document.createElement("p");
    empty.className = "subtle";
    empty.textContent = "本条 rollout 没有人工接管。";
    root.append(empty);
    return;
  }
  for (const phase of phases) {
    const row = document.createElement("p");
    row.className = "interval";
    row.textContent = phaseText(phase);
    root.append(row);
  }
}

function formatBytes(raw) {
  let value = Number(raw || 0);
  if (!Number.isFinite(value) || value < 0) value = 0;
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 2)} ${units[unit]}`;
}

function episodeFilename(episode) {
  return `episode_${String(episode.episode_index).padStart(6, "0")}.hdf5`;
}

function resetReplay() {
  state.previewGeneration += 1;
  const video = $("#episode-replay");
  video.pause();
  video.removeAttribute("src");
  video.load();
  $("#preview-state").textContent = "尚未生成";
  $("#preview-state").classList.remove("warning");
  $("#preview-message").textContent = "点击“生成 / 查看回放”即可读取三路相机记录。";
}

async function loadEpisode(episodeUuid) {
  const generation = ++state.episodeGeneration;
  if (!episodeUuid) {
    state.selectedEpisodeUuid = null;
    $("#replay-section").classList.add("hidden");
    $("#outcome-section").classList.add("hidden");
    updateButtons();
    return;
  }
  state.selectedEpisodeUuid = episodeUuid;
  $("#episode-select").value = episodeUuid;
  resetReplay();
  const labels = await request(
    `/api/episodes/${episodeUuid}/labels?${queryRoot()}`,
  );
  if (generation !== state.episodeGeneration || episodeUuid !== state.selectedEpisodeUuid) return;
  const episode = selectedEpisode();
  $("#selected-episode-summary").textContent = episode
    ? `${episodeFilename(episode)} · ${episode.frame_count} frames · ${formatBytes(episode.size_bytes)}`
    : episodeUuid;
  renderInterventions(labels.intervention_phases);
  const outcome = ["success", "failure"].includes(labels.episode_outcome)
    ? labels.episode_outcome
    : null;
  const outcomeState = $("#outcome-state");
  outcomeState.textContent = outcome === "success" ? "成功" : outcome === "failure" ? "失败" : "必须选择";
  outcomeState.classList.toggle("warning", !outcome);
  $("#replay-section").classList.remove("hidden");
  $("#outcome-section").classList.remove("hidden");
  updateButtons();
}

async function refreshEpisodes(preferredUuid = null) {
  if (!state.prepared) return;
  const allEpisodes = await request(`/api/episodes?${queryRoot()}`);
  const series = currentSeries();
  state.episodes = allEpisodes
    .filter(
      (item) =>
        item.task_id === series.task_id &&
        item.model_id === series.model_id &&
        item.dataset_round === series.dataset_round,
    )
    .sort((left, right) => left.episode_index - right.episode_index);
  const select = $("#episode-select");
  select.replaceChildren();
  for (const [displayIndex, episode] of state.episodes.entries()) {
    const option = document.createElement("option");
    option.value = episode.episode_uuid;
    option.textContent = `${displayIndex + 1} ? ${episodeFilename(episode)} · ${episodeOutcomeText(episode)}`;
    select.append(option);
  }
  const fallback = state.episodes.length
    ? state.episodes[state.episodes.length - 1].episode_uuid
    : null;
  const selected =
    preferredUuid && state.episodes.some((item) => item.episode_uuid === preferredUuid)
      ? preferredUuid
      : fallback;
  await loadEpisode(selected);
}

function renderPreviewStatus(status) {
  const label = $("#preview-state");
  label.textContent = status.state === "ready" ? "已就绪" : status.state === "error" ? "失败" : "生成中";
  label.classList.toggle("warning", status.state === "error");
  $("#preview-message").textContent = previewProgress(status);
}

async function pollPreview(episodeUuid, generation) {
  if (generation !== state.previewGeneration) return;
  try {
    const status = await request(
      `/api/episodes/${episodeUuid}/preview/status?${queryRoot()}`,
    );
    if (generation !== state.previewGeneration) return;
    if (generation !== state.previewGeneration || episodeUuid !== state.selectedEpisodeUuid) return;
    renderPreviewStatus(status);
    if (status.state === "ready") {
      $("#episode-replay").src = apiUrl(`/api/episodes/${episodeUuid}/preview.mp4?${queryRoot()}&cache=${Date.now()}`);
      return;
    }
    if (status.state === "error") return;
    window.setTimeout(() => pollPreview(episodeUuid, generation), 1000);
  } catch (error) {
    if (generation !== state.previewGeneration) return;
    $("#preview-state").textContent = "失败";
    $("#preview-state").classList.add("warning");
    $("#preview-message").textContent = error.message;
  }
}

async function requestPreview(episodeUuid = state.selectedEpisodeUuid) {
  if (!episodeUuid) return;
  if (episodeUuid !== state.selectedEpisodeUuid) await loadEpisode(episodeUuid);
  const generation = state.previewGeneration + 1;
  state.previewGeneration = generation;
  try {
    const status = await request(
      `/api/episodes/${episodeUuid}/preview?${queryRoot()}`,
      { method: "POST" },
    );
    if (generation !== state.previewGeneration || episodeUuid !== state.selectedEpisodeUuid) return;
    renderPreviewStatus(status);
    if (status.state === "ready") {
      $("#episode-replay").src = apiUrl(`/api/episodes/${episodeUuid}/preview.mp4?${queryRoot()}&cache=${Date.now()}`);
      return;
    }
    await pollPreview(episodeUuid, generation);
  } catch (error) {
    $("#preview-state").textContent = "失败";
    $("#preview-state").classList.add("warning");
    $("#preview-message").textContent = error.message;
  }
}

async function startRecording() {
  try {
    const prepared = await prepareStorage({ quiet: true });
    if (prepared.label_blocked) {
      message("请先为上一条 episode 选择成功或失败。", true);
      return;
    }
    await request("/api/episodes/start", {
      method: "POST",
      body: JSON.stringify(currentSeries()),
    });
    message("rollout 记录已开始。确认 Frames 增加后，再到部署终端按 Enter。", false);
    await refreshStatus();
  } catch (error) {
    message(error.message, true);
    await prepareStorage({ quiet: true }).catch(() => {});
  }
}

async function stopRecording() {
  message("正在停止采样并原子保存 HDF5，请稍候…", false);
  $("#stop-button").disabled = true;
  try {
    let stopped = await request("/api/episodes/stop", { method: "POST" });
    for (let attempt = 0; attempt < 600; attempt += 1) {
      const progress = stopProgress(stopped);
      message(progress.message, progress.failed);
      if (progress.failed) throw new Error(progress.message);
      if (progress.complete) break;
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      stopped = await request("/api/status");
    }
    const progress = stopProgress(stopped);
    if (!progress.complete) {
      throw new Error("HDF5 保存等待超时，请查看记录器状态。");
    }
    message(`${progress.message} 正在生成回放。`, false);
    await refreshStatus();
    await prepareStorage({ quiet: true, previewLatest: true });
  } catch (error) {
    message(error.message, true);
    await refreshStatus();
  }
}

async function setOutcome(outcome) {
  const uuid = state.selectedEpisodeUuid;
  if (!uuid) return;
  try {
    await request(`/api/episodes/${uuid}/outcome?${queryRoot()}`, {
      method: "POST",
      body: JSON.stringify(outcomeRequest(uuid, outcome)),
    });
    message(`已保存结果：${outcome === "success" ? "成功" : "失败"}。下一条 episode 已解锁。`, false);
    await prepareStorage({ quiet: true });
  } catch (error) {
    message(error.message, true);
  }
}

async function permanentlyDeleteSelected() {
  const episode = selectedEpisode();
  if (!episode) return;
  const filename = episodeFilename(episode);
  const size = formatBytes(episode.size_bytes);
  const accepted = window.confirm(
    `永久删除 ${filename}（${size}）？\n\nHDF5、标签和回放缓存都会被直接删除，不能恢复。删除中间项不会重排；删除末尾项后，该编号可由下一条复用。`,
  );
  if (!accepted) return;
  try {
    await request(`/api/episodes/${episode.episode_uuid}?${queryRoot()}`, {
      method: "DELETE",
      body: JSON.stringify(deleteRequest(episode.episode_uuid)),
    });
    message(`${filename} 已永久删除；下一条编号已按当前现存文件重新计算。`, false);
    state.selectedEpisodeUuid = null;
    resetReplay();
    await prepareStorage({ quiet: true });
  } catch (error) {
    message(error.message, true);
  }
}

function connectCameras() {
  for (const image of $$("img[data-camera]")) {
    const frame = image.closest(".camera-frame");
    const indicator = frame.querySelector(".camera-state");
    const connect = () => {
      indicator.textContent = "正在连接…";
      image.src = apiUrl(`/api/cameras/${image.dataset.camera}.mjpeg?retry=${Date.now()}`);
    };
    image.addEventListener("load", () => {
      frame.classList.add("online");
      indicator.textContent = "在线";
    });
    image.addEventListener("error", () => {
      frame.classList.remove("online");
      indicator.textContent = "画面暂不可用，正在重连…";
      window.setTimeout(connect, 1500);
    });
    connect();
  }
}

$("#series-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await prepareStorage();
  } catch (error) {
    state.prepared = null;
    updateButtons();
    message(error.message, true);
  }
});
$("#start-button").addEventListener("click", startRecording);
$("#stop-button").addEventListener("click", stopRecording);
$("#success-button").addEventListener("click", () => setOutcome("success"));
$("#failure-button").addEventListener("click", () => setOutcome("failure"));
$("#episode-select").addEventListener("change", (event) =>
  loadEpisode(event.target.value).catch((error) => message(error.message, true)),
);
$("#refresh-episodes").addEventListener("click", () =>
  refreshEpisodes(state.selectedEpisodeUuid).catch((error) =>
    message(error.message, true),
  ),
);
$("#replay-episode").addEventListener("click", () => requestPreview());
$("#delete-episode").addEventListener("click", permanentlyDeleteSelected);

restoreSeriesLocally();
if (REVIEW_ONLY) {
  const form = $("#series-form");
  const series = {
    data_root: "/media/agilex/Getea1/jiaan/data/cobot-realworld-vla/task5/plug-insertion-rlt-v1/raw",
    task_id: "plug_insertion", model_id: "openpi_rlt_plug_online_warmup",
    checkpoint_id: "step_4000", dataset_round: "plug_online_r2",
  };
  for (const name of Object.keys(series)) form.elements[name].value = series[name];
}
connectCameras();
refreshStatus();
prepareStorage({ quiet: true }).catch((error) => message(error.message, true));
window.setInterval(refreshStatus, 1000);
