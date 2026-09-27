"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const { buttonsFor, dataRootQuery, intervalsFromNodes, nodeLabel, versionRequest } = window.SegmentedTeachUI;
const {
  parseApiResponse, rltButtons, rltGuidance, rltShortcutAction,
  releaseOptionLabel, releaseSwitchAllowed,
} = window.CobotConsoleUI;
let current = { capture_state: "idle", generation: 0, nodes: [] };
let renderedGeneration = -1;
let currentReview = null;
let viewedEpisode = current;
let historyUuid = null;
let selectionToken = 0;
let replayTimer = null;
let replayTimeline = null;
let storagePrepared = false;
let consoleStatus = null;
let rltSession = null;
let consoleRefreshBusy = false;
let rltStorageDirty = false;
let rltStorageEditable = false;
let rltHistoryCount = null;
let rltEpisodeSummaryByUuid = new Map();
let rltRecorderRefreshBusy = false;
let rltHistoryRefreshPending = false;
let lastRltRecorderSignature = "";
let STORAGE_KEY = "cobot-data-console-config";
let normalPathPicker = null;
let rltPathPicker = null;
let diagnosticsPoller = null;
let cameraViewState = null;
let cameraStatusRequestPending = false;
let cameraFailures = 0;
let cameraTimer = null;
let devicePoller = null;
let releaseListing = null;
let releaseSwitching = false;
let modelListing = null;
let modelSwitching = false;
let captureHomeBusy = false;
let captureShortcutBusy = false;
let rltShortcutBusy = false;
let rltOperationBusy = false;
let rltOperationToken = 0;
let manualHomeBusy = false;
let selectionRefreshTimer = null;
let historyRefreshToken = 0;
let historyEpisodes = [];
let mediaPreloadToken = 0;
const preloadedVideos = new Map();
const preloadedFrames = new Map();
const preloadedEpisodeDetails = new Map();
const preloadedRltLabels = new Map();
const HISTORY_INITIAL_PAGE = 24;
const HISTORY_BACKGROUND_PAGE = 100;
const deviceCompletionSeen = new Map();
const deviceHealthSeen = new Map();
let persistentCameraPanel = null;
const cameraPanelMounts = new Map();
let episodeBrowserMode = null;
let pendingCollectionMode = null;
let collectionModeTask = null;

function setButtonBusy(button, busy, label = "") {
  if (!button) return;
  button.classList.toggle("is-loading", Boolean(busy));
  button.setAttribute("aria-busy", busy ? "true" : "false");
  if (label) button.dataset.busyLabel = label;
  else delete button.dataset.busyLabel;
  window.CobotUnifiedCollection?.render();
}

async function withButtonBusy(button, task, label = "处理中") {
  setButtonBusy(button, true, label);
  try { return await task(); }
  finally { setButtonBusy(button, false); }
}

function textSelectionActive() {
  const selection = window.getSelection ? window.getSelection() : null;
  return Boolean(selection && !selection.isCollapsed && String(selection).trim());
}

function scheduleAfterSelection() {
  clearTimeout(selectionRefreshTimer);
  if (textSelectionActive()) return;
  selectionRefreshTimer = setTimeout(() => {
    if (textSelectionActive()) return;
    refresh();
    refreshConsole();
    if (["learning", "training"].includes(activePageName())) diagnosticsPoller.tick();
    if (activePageName() === "system") devicePoller.tick();
  }, 80);
}

function message(text, failed = false) {
  $("#message").textContent = text;
  $("#message").classList.toggle("error", failed);
  window.CobotUnifiedCollection?.render();
}

async function request(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  return parseApiResponse(response);
}

function consoleMessage(text, failed = false) {
  $("#console-message").textContent = text;
  $("#console-message").classList.toggle("error", failed);
}
function rltMessage(text, failed = false) {
  $("#rlt-message").textContent = text;
  $("#rlt-message").classList.toggle("error", failed);
  window.CobotUnifiedCollection?.render();
}
function historyIsRlt() {
  return (pendingCollectionMode || consoleStatus?.selected_mode) === "rlt";
}
function historyDataRoot() {
  if (!historyIsRlt()) return selectedDataRoot();
  const sessionRoot = rltSession && rltSession.data_root;
  return String(sessionRoot || $("#rlt-recording-directory").textContent || "").trim();
}
function historyEndpoint(path) {
  const prefix = historyIsRlt() ? "/api/rlt-recorder/api" : "/api/segmented-teach";
  const separator = path.includes("?") ? "&" : "?";
  return `${prefix}${path}${separator}${dataRootQuery(historyDataRoot())}`;
}
function cachedFrameSource(url) { return preloadedFrames.get(url) || url; }
function rememberFrame(url,blob){
  if(preloadedFrames.has(url))return;
  preloadedFrames.set(url,URL.createObjectURL(blob));
  while(preloadedFrames.size>900){const oldest=preloadedFrames.keys().next().value;URL.revokeObjectURL(preloadedFrames.get(oldest));preloadedFrames.delete(oldest);}
}
function episodeDetailKey(uuid) { return historyDataRoot()+"|"+uuid; }
function historyMessage(text, failed = false) {
  if (historyIsRlt()) rltMessage(text, failed);
  else message(text, failed);
}
function mountEpisodeBrowser(isRlt) {
  const normalMount = $("#episode-browser-operation");
  const normalLayout = $("[data-page=operation] .capture-layout");
  const actionPanel = $("[data-page=operation] .capture-layout > .action-panel");
  if (normalMount && normalLayout && actionPanel && normalMount.parentElement !== normalLayout) normalLayout.insertBefore(normalMount, actionPanel);
  const panel = $("#episode-browser-panel");
  const mount = $(window.CobotUnifiedCollection?.mounted ? "#episode-browser-operation" : isRlt ? "#episode-browser-learning" : "#episode-browser-operation");
  if (panel && mount && panel.parentElement !== mount) mount.appendChild(panel);
  if (episodeBrowserMode === isRlt) return;
  episodeBrowserMode = isRlt;
  historyUuid = null;
  window.CobotFeaturePaths?.update("history",{rlt:isRlt,dataRoot:historyDataRoot()});
  currentReview = null;
  resetReplay();
  const endpoints=$('#episode-endpoints');if(endpoints)endpoints.remove();
  $("#timeline").replaceChildren();
  $("#node-preview").classList.add("hidden");
  $("#review-panel").classList.add("hidden");
  panel.classList.toggle("is-rlt-history",isRlt);
  $("#episode-browser-title").textContent = isRlt ? "数据" : "采集历史";
  $("#episode-browser-subtitle").textContent = isRlt
    ? "当前 RLT 目录 · 自主 rollout 与 HIL 记录"
    : "普通采集 Episode";
  $("#episode-live-strip").classList.toggle("hidden", !isRlt);
  $("#save-review").classList.toggle("hidden", isRlt);
  $("#review-note").disabled = isRlt;
  refreshHistory({loadSelected: true}).catch(error => historyMessage(error.message, true));
}
function updateEpisodeBrowserControls() {
  const isRlt = historyIsRlt();
  const active = isRlt
    ? Boolean(rltSession && ["finalizing", "replay_committing"].includes(String(rltSession.phase || "")))
    : ["recording", "paused", "finalizing"].includes(current.capture_state);
  $("#delete-history").disabled = active || !$("#episode-history").value;
}
function rltHistoryNodes(labels, frameCount = 0) {
  const phases = Array.isArray(labels.intervention_phases) ? labels.intervention_phases : [];
  const last = Math.max(0, Math.trunc(Number(frameCount || 1)) - 1);
  const candidates = [
    {frame_index: 0, node_kind: "summary", endpoint: "start", display_label: "开始 · frame 0"},
    ...phases.map((phase, index) => ({
      frame_index: Number(phase.start_frame || 0),
      node_kind: "hil",
      display_label: `HIL ${index + 1} · ${Array.isArray(phase.human_sides) ? phase.human_sides.join("+") : "human"} · frames ${phase.start_frame}–${phase.end_frame}`,
    })),
    ...(labels.operator_nodes || []).map(node=>({...node, operator:true, display_label: `${({pause:'暂停',resume:'继续',marker:'节点'})[node.node_kind]||'节点'} · frame ${node.frame_index}`})),
    {frame_index: last, node_kind: "summary", endpoint: "end", display_label: `结束 · frame ${last}`},
  ];
  const seen = new Set();
  return candidates
    .sort((left, right) => left.frame_index - right.frame_index)
    .filter(node => node.endpoint || node.operator || (node.frame_index > 0 && node.frame_index < last && !seen.has(node.frame_index) && seen.add(node.frame_index)))
    .map((node, index) => ({
      ...node,
      node_id: index + 1,
      sample_timestamp: node.frame_index / 10,
    }));
}
function historyNodeLabel(node) {
  return node.display_label || nodeLabel(node, 30);
}
function renderConsole() {
  if (!consoleStatus) return;
  const counts = consoleStatus.recording_counts || {};
  $("#recording-counts").textContent = "已录制：" + [["demonstrations", "专家"], ["warmup", "warmup"], ["online", "在线"]].map(([key, name]) => name + " " + String((counts[key] || {}).total || 0)).join(" · ");
  const isRlt = historyIsRlt();
  mountEpisodeBrowser(isRlt);
  $("#selected-mode").textContent = isRlt ? "RLT rollout" : "普通采集";
  $("#active-mode").textContent = consoleStatus.active_mode || "idle";
  const readiness = consoleStatus.ros_readiness || {};
  $("#ros-readiness").textContent = readiness.status === "ok"
    ? "ready" : String(readiness.error_code || readiness.status || "unknown");
  $("#rlt-backend-phase").textContent = String(consoleStatus.rlt_backend_phase || "offline");
  $("#rlt-model-release").textContent = String((consoleStatus.rlt_model || {}).status || "legacy");
  $("#mode-normal").classList.toggle("selected", !isRlt);
  $("#mode-rlt").classList.toggle("selected", isRlt);
  const writerBusy = Boolean(consoleStatus.active_mode);
  $("#mode-normal").disabled = writerBusy && isRlt;
  $("#mode-rlt").disabled = consoleStatus.rlt_enabled === false || (writerBusy && !isRlt);
  for (const panel of $$(".normal-only")) panel.classList.toggle("hidden", isRlt);
  for (const panel of $$(".rlt-only")) panel.classList.toggle("hidden", !isRlt);
  $("#rlt-session-phase").textContent = rltSession ? String(rltSession.phase || "unknown") : "";
  $("#rlt-session-phase").classList.toggle("hidden",!rltSession);
  $("#rlt-actor-version").textContent = rltSession && rltSession.actor_version != null
    ? String(rltSession.actor_version) : "—";
  $("#rlt-episode-id").textContent = rltSession && rltSession.episode_display_number != null
    ? String(rltSession.episode_display_number) : "—";
  $("#rlt-policy-state").textContent = rltSession && !rltSession.policy_paused ? "running" : "paused";
  const rolloutPhase = String((rltSession && rltSession.phase) || "");
  const pausedHighlighted = Boolean(rltSession && rltSession.policy_paused &&
    !["disarmed", "ready", "ready_disarmed", "waiting_scene", "stopped"].includes(rolloutPhase));
  $("#rlt-pause").classList.toggle("is-active", pausedHighlighted);
  $("#rlt-resume").classList.toggle("is-active", Boolean(rltSession && !rltSession.policy_paused && rolloutPhase === "rollout"));
  $("#rlt-saved-count").textContent = String(rltHistoryCount == null
    ? (rltSession ? rltSession.recorded_episode_count || 0 : 0)
    : rltHistoryCount);
  if (rltSession && rltSession.data_root) $("#rlt-recording-directory").textContent = rltSession.data_root;
  $("#rlt-guidance").textContent = rltGuidance(consoleStatus, rltSession);
  $("#top-mode").textContent = isRlt ? "RLT" : "CAPTURE";
  $("#top-session").textContent = rltSession ? String(rltSession.phase || "—") : "—";
  $("#top-actor").textContent = rltSession && rltSession.actor_version != null ? String(rltSession.actor_version) : "—";
  const topRoot = isRlt ? $("#rlt-recording-directory").textContent : selectedDataRoot();
  $("#top-data-root").textContent = topRoot || "—";
  $("#sys-ros").textContent = $("#ros-readiness").textContent;
  $("#sys-rlt").textContent = $("#rlt-backend-phase").textContent;
  $("#sys-recorder").textContent = String(consoleStatus.recorder_state || "unknown");
  const buttons = rltButtons(consoleStatus, rltSession);
  for (const name of Object.keys(buttons)) $("#rlt-" + name).disabled = !buttons[name] || ((rltOperationBusy||manualHomeBusy) && name !== 'stop');
  updateManualHomeControls();
  renderReleasePicker();
  renderModelPicker();
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.sessionStatus(consoleStatus,rltSession);
  updateEpisodeBrowserControls();
  window.CobotUnifiedCollection?.render({capture:current,console:consoleStatus,session:rltSession,busy:captureHomeBusy||manualHomeBusy||rltOperationBusy});
}
function renderReleasePicker() {
  const select = $("#rlt-release-select");
  if (releaseListing) {
    const names = releaseListing.releases.map(item => item.name).join("|");
    if (select.dataset.names !== names) {
      const keep = select.value || releaseListing.current;
      select.replaceChildren(...releaseListing.releases.map(item => { const option = document.createElement("option"); option.value = item.name; option.textContent = releaseOptionLabel(item); return option; }));
      select.dataset.names = names;
      select.value = releaseListing.releases.some(item => item.name === keep) ? keep : releaseListing.current;
    }
    for (const option of select.options) option.textContent = releaseOptionLabel(releaseListing.releases.find(item => item.name === option.value));
  }
  $("#rlt-release-switch").disabled = !releaseSwitchAllowed(consoleStatus, rltSession, releaseListing, select.value, releaseSwitching);
}
async function refreshReleases() {
  try { releaseListing = await request("/api/rlt/releases"); renderReleasePicker(); }
  catch (error) { $("#rlt-release-note").textContent = "release 列表不可用：" + error.message; }
}
async function switchRelease() {
  const name = $("#rlt-release-select").value;
  if (!name || !window.confirm(window.CobotPreferences.text("切换到 " + name + "？\n会结束当前 Session 并重新加载 actor（模型不重载）。"))) return;
  releaseSwitching = true; renderReleasePicker(); rltMessage("正在切换 actor release…");
  try {
    const result = await request("/api/rlt/release", { method: "POST", body: JSON.stringify({ release: name }) });
    rltMessage(result.message || "已切换");
  } catch (error) { rltMessage("切换失败：" + error.message, true); }
  finally { releaseSwitching = false; await refreshReleases(); }
}
function selectedModel() {
  if (!modelListing) return null;
  return (modelListing.models || []).find(item => item.id === modelListing.current) || null;
}
let lastCoreParameters = [];
const coreGroupZh = {Model:'模型',Networks:'网络',"Online RL":'在线强化学习',Online:'在线训练',Replay:'回放',Runtime:'运行',"Stage 1":'第一阶段',Validation:'验证',Warmup:'预热',Other:'其他'};
const coreKeyZh = {
  cameras:'相机',"model action dim":'模型动作维度',"model horizon":'模型预测长度',"physical arm":'执行臂',
  "RL token z dim":'RL token 维度',"state / action dim":'状态 / 动作维度',actor:'策略网络',critic:'价值网络',
  "actor / critic lr":'策略 / 价值学习率',"actor period":'策略发布周期',"target tau":'目标网络系数',
  "chunk length":'动作片段长度',"delta weight":'动作改变量权重',"exploration std":'探索标准差',gamma:'折扣因子',
  "online BC / Q":'在线 BC / Q 权重',"reference dropout":'参考动作丢弃率',"warmup BC / Q":'预热 BC / Q 权重',
  "batch size":'批大小',"checkpoint interval":'检查点间隔',"publish interval":'发布间隔',
  "updates / cycle":'每轮更新步数',capacity:'容量',"actor pull interval":'策略拉取间隔',
  "control rate":'控制频率',"executed horizon":'执行长度',"safe fallback":'安全回退',
  "checkpoint step":'检查点步数',"FSDP devices":'FSDP 设备数',"global batch":'全局批大小',
  seed:'随机种子',"training steps":'训练步数',"steady inference":'稳定推理',
  "minimum replay":'最小回放量',"training budget":'训练预算'
};
function renderCoreParameters(rows) {
  const root = $("#rlt-core-parameters"); if (!root) return;
  lastCoreParameters = rows || [];
  const chinese = window.CobotPreferences?.language !== 'en';
  root.replaceChildren();
  const groups = new Map();
  for (const row of rows || []) {
    const group = String(row.group || "Other");
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(row);
  }
  for (const [name, values] of groups) {
    const card = document.createElement("section");
    const title = document.createElement("h4"); title.textContent = chinese ? coreGroupZh[name] || name : name; card.append(title);
    const list = document.createElement("dl");
    for (const row of values) {
      const key = document.createElement("dt"); key.textContent = chinese ? coreKeyZh[row.key] || row.key : row.key;
      const value = document.createElement("dd");
      const shown = chinese && row.key === 'physical arm' && row.value === 'right' ? '右臂' : row.value;
      value.textContent = String(shown) + (row.unit ? " " + row.unit : "");
      list.append(key, value);
    }
    card.append(list); root.append(card);
  }
}
document.addEventListener('cobot:language',()=>renderCoreParameters(lastCoreParameters));
function renderModelPicker() {
  const select = $("#rlt-model-select"); if (!select || !modelListing) return;
  window.CobotUnifiedCollection?.updateCatalog(modelListing);
  if(window.CobotWorkbenchUI)window.CobotWorkbenchUI.updateModels(modelListing);
  const models = modelListing.models || [];
  const signature = models.map(item => item.id + ":" + item.available + ":" + item.status).join("|");
  if (select.dataset.signature !== signature) {
    select.replaceChildren(...models.map(item => {
      const option = document.createElement("option"); option.value = item.id;
      option.disabled = !item.available;
      option.textContent = item.label + (item.available ? "" : " / " + item.status);
      return option;
    }));
    select.dataset.signature = signature;
  }
  if (models.some(item => item.id === modelListing.current)) select.value = modelListing.current;
  const chosen = models.find(item => item.id === select.value) || selectedModel();
  const currentModel = selectedModel();
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.modelStatus(currentModel);
  window.CobotFeaturePaths?.update("rl",{model:currentModel,chosen,dataRoot:$("#rlt-recording-directory").textContent});
  $("#rlt-model-status").textContent = currentModel ? currentModel.status : "unavailable";
  $("#rlt-model-note").textContent = chosen
    ? chosen.description + " Checkpoint: " + (chosen.checkpoint || "—")
    : "当前 profile 没有已登记模型。";
  renderCoreParameters((chosen || currentModel || {}).parameters || []);
  const safePhase = !rltSession || ["offline","disarmed","ready","stopped","waiting_scene"].includes(String(rltSession.phase || "offline"));
  $("#rlt-model-switch").disabled = modelSwitching || !chosen || !chosen.available || chosen.id === modelListing.current || !safePhase || Boolean(consoleStatus && consoleStatus.active_mode);
  const target = currentModel && currentModel.start_target;
  $("#device-rlt-reference").disabled = !currentModel || !currentModel.available || target !== "reference";
  $("#device-rlt-warmup").disabled = !currentModel || !currentModel.available || currentModel.id !== "plug_v3-stage1-reference";
  $("#device-rlt-online").disabled = !currentModel || !currentModel.available || target !== "online";
  $("#device-rlt-frozen").disabled = !currentModel || !currentModel.available || target !== "frozen";
  const legacy = $("#legacy-release-picker");
  if (legacy) legacy.classList.toggle("hidden", modelListing.profile === "plug_v3_yyshadow");
}
async function refreshModels() {
  try { modelListing = await request("/api/rlt/models"); renderModelPicker(); }
  catch (error) { $("#rlt-model-note").textContent = "模型目录不可用：" + error.message; }
}
async function switchModel() {
  const id = $("#rlt-model-select").value;
  if (!id) return;
  modelSwitching = true; renderModelPicker();
  try {
    modelListing = await request("/api/rlt/model", {method:"POST", body:JSON.stringify({model_id:id})});
    rltMessage("模型已选择；下一次启动使用 " + id + "。");
    await refreshConsole();
  } catch (error) { rltMessage("模型未切换：" + error.message, true); }
  finally { modelSwitching = false; renderModelPicker(); }
}
async function refreshConsole() {
  if (consoleRefreshBusy) return;
  consoleRefreshBusy = true;
  try {
    consoleStatus = await request("/api/console/status");
    const phase = String(consoleStatus.rlt_backend_phase || "offline");
    const children = (consoleStatus.rlt_lifecycle || {}).children || {};
    const preloadOnly = children.model && !children.session;
    if (!preloadOnly &&
        !["offline", "fault", "stopping"].includes(phase) &&
        !phase.startsWith("loading")) {
      try {
        rltSession = await request("/api/rlt/session");
      } catch (error) {
        rltSession = null;
        rltMessage("RLT 后端暂不可用：" + error.message, true);
      }
    } else {
      rltSession = null;
    }
    if (consoleStatus.selected_mode === "rlt") await refreshRltStorage();
    renderConsole();
    consoleMessage(consoleStatus.rlt_enabled === false && consoleStatus.rlt_model
      ? "控制台已连接；新三视角模型尚未完成验证与部署，RLT 暂未开放。" : "统一控制台已连接。", false);
  } catch (error) {
    consoleMessage("统一控制台不可用：" + error.message, true);
  } finally {
    consoleRefreshBusy = false;
  }
}
async function refreshRltStorage() {
  const storage = await request("/api/rlt/storage");
  $("#rlt-recording-directory").textContent = storage.data_root;
  window.CobotFeaturePaths?.update("rl",{model:selectedModel(),dataRoot:storage.data_root});
  if (!rltStorageDirty) $("#rlt-data-root").value = storage.data_root;
  rltStorageEditable = storage.editable;
  $("#rlt-data-root").disabled = !storage.editable;
  $("#rlt-save-storage").disabled = !storage.editable;
  if (rltPathPicker) {
    rltPathPicker.setDisabled(!storage.editable);
    if (!rltStorageDirty) rltPathPicker.remember(storage.data_root);
  }
  const count = rltSession ? rltSession.recorded_episode_count : null;
  if (rltHistoryCount === null) await refreshRltHistory();
  if (count !== null) rltHistoryCount = count;
}
async function refreshRltHistory() {
  const payload = await request("/api/rlt/history");
  const kept = (payload.episodes || []).filter(item => String(item.outcome || "").toLowerCase() !== "aborted");
  if (kept.length || !historyEpisodes.length) rltHistoryCount = kept.length;
  rltEpisodeSummaryByUuid = new Map(kept.map(item => [item.episode_uuid, item]));
  $("#rlt-saved-count").textContent = String(rltHistoryCount || 0);
  if (historyIsRlt() && episodeBrowserMode === true) {
    await refreshHistory({loadSelected: !historyUuid || !document.querySelector('#episode-endpoints img')});
  }
}
async function refreshRltRecorderBrowser() {
  if (rltRecorderRefreshBusy || !historyIsRlt() || activePageName() !== "operation") return;
  rltRecorderRefreshBusy = true;
  try {
    const status = await request("/api/rlt-recorder/api/status");
    window.CobotUnifiedCollection?.updateRecorder(status);
    const state = String(status.state || "unknown");
    const left = String(status.control_source_left || "unknown");
    const right = String(status.control_source_right || "unknown");
    const hil = left === "human" || right === "human";
    const recording = ["starting", "recording", "stopping"].includes(state);
    $("#episode-live-strip").classList.remove("hidden");
    $("#episode-live-dot").classList.toggle("ok", recording);
    $("#episode-live-dot").classList.toggle("error", ["error", "fatal"].includes(state));
    $("#episode-live-badge").textContent = recording
      ? (hil ? "正在录制 HIL" : "正在录制自主 rollout")
      : `录制器 ${state}`;
    $("#episode-live-frames").textContent = `${Number(status.frames_written || status.frames_sampled || 0)} 帧`;
    $("#episode-live-control").textContent = hil
      ? `HIL · ${left}/${right}`
      : `自主 · ${left}/${right}`;
    const signature = [state, status.episode_file || status.path || "", status.frames_written || 0].join(":");
    if (rltHistoryRefreshPending && !recording && signature !== lastRltRecorderSignature) {
      rltHistoryRefreshPending = false;
      rltHistoryCount = null;
      await refreshRltHistory();
    }
    lastRltRecorderSignature = signature;
  } catch (error) {
    $("#episode-live-dot").classList.remove("ok");
    $("#episode-live-dot").classList.add("error");
    $("#episode-live-badge").textContent = "RLT 录制状态不可用";
    $("#episode-live-control").textContent = error.message;
  } finally {
    rltRecorderRefreshBusy = false;
  }
}
async function saveRltStorage() {
  try {
    const payload = await request("/api/rlt/storage", {method:"POST", body:JSON.stringify({data_root:$("#rlt-data-root").value.trim()})});
    rltStorageDirty = false; rltHistoryCount = null;
    if (rltPathPicker) rltPathPicker.remember(payload.data_root);
    rltMessage("录制目录已设置：" + payload.data_root);
    await refreshConsole();
  } catch (error) { rltMessage("目录未设置：" + error.message, true); }
}
async function selectConsoleMode(mode) {
  try {
    consoleStatus = await request("/api/console/mode", {
      method: "POST", body: JSON.stringify({ mode }),
    });
    rltSession = null;
    rltHistoryCount = null;
    return true;
  } catch (error) {
    consoleMessage("无法切换模式：" + error.message, true);
    return false;
  }
}
async function operateRlt(name,{forceHome=false}={}) {
  if((rltOperationBusy||manualHomeBusy) && name!=='stop')return;
  const operationToken=++rltOperationToken;
  rltOperationBusy=true;
  window.CobotRltHome.setBusy(true);renderConsole();
  const terminal=['success','failure','abort','save'].includes(name);
  const selectedHome=terminal?{...window.CobotCaptureHome.selection(),...((forceHome||['success','failure'].includes(name))?{enabled:true}:{})}:null;
  let terminalAccepted=false;
  const paths = {
    arm: "session/arm", start: "session/start", pause: "session/pause",
    resume: "session/resume", stop: "session/stop", next: "episode/next",
    success: "episode/success", failure: "episode/failure", abort: "episode/abort",
    prepare: "session/prepare", marker: "episode/marker", save: "episode/save",
  };
  try {
    const body = { episode_id: rltSession.episode_id, generation: rltSession.generation };
    if (terminal) {
      // The console owns the chosen target. Disable the backend's fixed home preset.
      body.home_after_terminal = false;
    }
    const result = await request("/api/rlt/" + paths[name], {
      method: "POST", body: JSON.stringify(body),
    });
    terminalAccepted=terminal;
    if(operationToken!==rltOperationToken)return;
    rltSession=result;
    rltMessage(name + " 已完成。", false);
    if (terminal) {
      rltHistoryRefreshPending = true;
      await window.CobotRltHome.run({
        selection:selectedHome,terminal:{...rltSession},cancelled:()=>operationToken!==rltOperationToken,
        readSession:()=>request('/api/rlt/session'),
        readDevices:()=>request('/api/console/devices'),
        startHome:spec=>window.CobotDeviceUI.execute(spec,'按所选机械臂和位姿自动归位',true),
        onPhase:(phase,spec)=>{
          const text={waiting:'本轮已结束，等待提交后归位…',homing:`正在归位到 ${spec?.pose||selectedHome.pose}…`,complete:`归位完成：${spec?.pose||selectedHome.pose}，可以开始下一轮。`}[phase];
          rltMessage(text,false);
          window.CobotWorkspaceUI?.report(text,phase==='complete'?'success':'running','归位');
        },
      });
    }
  } catch (error) {
    if(operationToken===rltOperationToken){
      const text=(terminalAccepted?'本轮已结束；自动归位未完成：':name+' 结果待确认：')+error.message;
      rltMessage(text,true);window.CobotWorkspaceUI?.report(text,'error','归位');
    }
  } finally {
    if(operationToken===rltOperationToken){rltOperationBusy=false;window.CobotRltHome.setBusy(false);renderConsole();await refreshConsole();}
  }
}

let directorySuggestionToken = 0;
let directorySuggestionTimer = null;
function hideDirectorySuggestions() {
  directorySuggestionToken++;
  $("#directory-browser").classList.add("hidden");
}
async function suggestDirectories() {
  const input = $("#capture-form").elements.namedItem("data_root");
  if (input.disabled) return;
  const value = input.value.trim();
  const token = ++directorySuggestionToken;
  $("#directory-browser").classList.remove("hidden");
  $("#directory-hint").textContent = "正在读取目录…";
  $("#directory-options").replaceChildren();
  try {
    const result = await request("/api/segmented-teach/storage/directories?path=" + encodeURIComponent(value));
    if (token !== directorySuggestionToken || value !== input.value.trim()) return;
    $("#directory-hint").textContent = !result.exists
      ? "目录不存在；点击“检查/新建目录”即可创建。"
      : result.directories.length
        ? result.directory + "/ — 点击选择子目录" + (result.truncated ? "（仅显示前256项，可继续输入筛选）" : "")
        : "没有匹配子目录；可填写新名称，再点击“检查/新建目录”。";
    for (const path of result.directories) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = path.split("/").pop() + "/";
      button.addEventListener("click", () => {
        if (input.disabled) return;
        input.value = path + "/";
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.focus();
      });
      $("#directory-options").append(button);
    }
  } catch (error) {
    if (token !== directorySuggestionToken || value !== input.value.trim()) return;
    $("#directory-hint").textContent = "无法列出目录：" + error.message;
  }
}
function scheduleDirectorySuggestions() {
  directorySuggestionToken++;
  window.clearTimeout(directorySuggestionTimer);
  directorySuggestionTimer = window.setTimeout(suggestDirectories, 180);
}

function startPayload() {
  const use_model=Boolean($('#capture-use-model')?.checked);
  return { data_root: selectedDataRoot(), storage_layout: "flat",use_model,
    ...(use_model?window.CobotCollectionModel.identity():{}),
    collection_model_id:use_model?$('#capture-model-select').value:null };
}

function selectedDataRoot() {
  // Disabled controls are omitted by FormData.  Save+Home intentionally locks
  // this input while finalizing, so read its stable value directly.
  const form = $("#capture-form");
  const input = form && form.elements && form.elements.namedItem("data_root");
  if (input) return String(input.value || "").trim();
  return String(new FormData(form).get("data_root") || "").trim();
}

function withDataRoot(path) {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}${dataRootQuery(selectedDataRoot())}`;
}

function saveConfig() {
  const root = selectedDataRoot();
  const previous = previousRecordingDirectories();
  const paths = [...new Set([root, ...(Array.isArray(previous) ? previous : [])])].slice(0, 30);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ data_root: root }));
  window.localStorage.setItem("cobot-recording-directories", JSON.stringify(paths));
  renderRecordingDirectories(paths);
}

function renderRecordingDirectories(paths) {
  const list = $("#recording-directories");
  if (!list) return;
  list.replaceChildren();
  for (const path of [...new Set(paths)]) {
    const option = document.createElement("option");
    option.value = path;
    list.append(option);
  }
}
function previousRecordingDirectories() {
  try {
    const paths = JSON.parse(window.localStorage.getItem("cobot-recording-directories") || "[]");
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
    const previous = Array.isArray(paths) ? paths.filter(p => typeof p === "string") : [];
    if (saved && typeof saved.data_root === "string") previous.push(saved.data_root);
    return previous;
  } catch (_error) { return []; }
}


function updateButtons() {
  window.CobotCollectionModel?.updateCapture(current);
  const homeBusy=captureHomeBusy||manualHomeBusy;
  const buttons = current.buttons || buttonsFor(current.capture_state);
  const active = ["recording", "paused", "finalizing"].includes(current.capture_state);
  const locked = active || homeBusy;
  $("#start").disabled = homeBusy || !storagePrepared || !["idle", "committed"].includes(current.capture_state);
  if ($('#capture-use-model').checked && !window.CobotCollectionModel?.ready()) $('#start').disabled=true;
  window.CobotCollectionModel?.render();
  $("#prepare-storage").disabled = locked;
  $("#browse-directories").disabled = locked;
  if (active) hideDirectorySuggestions();
  for (const input of $$("#capture-form input")) input.disabled = locked;
  if (normalPathPicker) normalPathPicker.setDisabled(locked);
  $("#delete-history").disabled = locked || !$("#episode-history").value;
  for (const name of ["pause", "resume", "marker", "stop", "discard"]) {
    $("#" + name).disabled = homeBusy || !buttons[name];
  }
  $("#stop-home").disabled = homeBusy || !buttons.stop;
  const homeEnabled = $("#capture-home-enabled").checked;
  const english=window.CobotPreferences?.language==='en';
  for(const id of ['stop-home','stop','discard']){
    const discard=id==='discard',button=$('#'+id);
    const zh=discard?(homeEnabled?'结束放弃并复位':'结束并放弃'):(homeEnabled?'结束保存并复位':'结束并保存');
    const en=discard?(homeEnabled?'Discard and home':'Discard episode'):(homeEnabled?'Save and home':'Finish and save');
    button.dataset.zh=zh;button.dataset.en=en;button.textContent=english?en:zh;
  }
  updateManualHomeControls();
  updateEpisodeBrowserControls();
  window.CobotUnifiedCollection?.render({capture:current,console:consoleStatus,session:rltSession,busy:captureHomeBusy||manualHomeBusy||rltOperationBusy});
}

async function refreshCaptureHomePoses() {
  try {
    const payload=await request('/api/console/devices');
    window.CobotCaptureHome.setCatalog(payload.home_poses||{});
    window.CobotRltHome.setCatalog(payload.home_poses||{});
  } catch (_error) { /* The unavailable pose remains visible; movement validates again. */ }
}
async function startCaptureHome(selection) {
  const payload=await request('/api/console/devices');
  window.CobotCaptureHome.setCatalog(payload.home_poses||{});
  const spec=window.CobotRltHome.operation(selection,payload.home_poses||{});
  if(!spec)return null;
  return window.CobotDeviceUI.execute(spec,`机械臂移动到 ${selection.pose}`,true);
}
async function waitForCaptureHome(job) {
  for (let count = 0; count < 480; count += 1) {
    await new Promise(resolve => setTimeout(resolve, 250));
    const payload = await request("/api/console/devices");
    const currentHome = (payload.jobs || {}).home;
    if (currentHome && currentHome.job_id === job.job_id &&
        ["completed", "failed", "stopped", "stale"].includes(currentHome.phase)) return currentHome;
  }
  throw new Error("复位等待超时；请查看输出中的归位任务");
}

async function revealSavedEpisode(episodeUuid, wasReviewing) {
  const select = $("#episode-history");
  if (!episodeUuid || !Array.from(select.options).some(option => option.value === episodeUuid)) return;
  select.value = episodeUuid;
  if (wasReviewing) await loadHistory();
}

async function stopAndPrepareCapture({ revealHistory = true, outcome = null } = {}) {
  const wasReviewing = Boolean(historyUuid);
  const stopped = await request("/api/segmented-teach/stop", {
    method: "POST", body: JSON.stringify({...versionRequest(current),outcome}),
  });
  const episodeUuid = stopped.episode_uuid || current.episode_uuid;
  render(stopped);
  showLive();
  await prepareStorage(true);
  if (revealHistory) await revealSavedEpisode(episodeUuid, wasReviewing);
  updateButtons();
  return { stopped, episodeUuid, wasReviewing };
}

async function finishCapture(discard=false,label=null,forceHome=false) {
  if(captureHomeBusy||manualHomeBusy)return;
  const selection={...window.CobotCaptureHome.selection(),...(forceHome?{enabled:true}:{})};
  let finished=false,savedEpisode=null;
  const outcome=discard?'本轮已放弃':'数据已保存';
  captureHomeBusy=true;updateButtons();
  try {
    message(discard?'正在结束并放弃本轮…':'正在保存本轮数据…',false);
    if(discard){
      const result=await request('/api/segmented-teach/discard',{method:'POST',body:JSON.stringify(versionRequest(current))});
      finished=true;render(result);showLive();await prepareStorage(true);
    }else{
      savedEpisode=await stopAndPrepareCapture({revealHistory:false,outcome:label||(window.CobotUnifiedCollection?.labelResults()?'unknown':null)});finished=true;
    }
    if(selection.enabled){
      message(`${outcome}，正在复位到 ${selection.pose}…`,false);
      const job=await startCaptureHome(selection);
      if(!job)throw Error('归位任务未启动，请查看输出');
      const completed=await waitForCaptureHome(job);
      if(completed?.phase!=='completed')throw Error('复位未完成：'+String(completed?.log_tail||completed?.phase||'unknown').slice(-240));
    }
    message(`${outcome}${selection.enabled?'，已复位到 '+selection.pose:''}，可以开始下一条。`,false);
  }catch(error){
    message((finished?outcome+'；':'')+error.message,true);await refresh();
  }finally{
    if(finished){await prepareStorage(true);if(savedEpisode)await revealSavedEpisode(savedEpisode.episodeUuid,savedEpisode.wasReviewing);}
    captureHomeBusy=false;updateButtons();
  }
}
async function finalizeAndHome(){return finishCapture(false);}
async function discardAndHome(){return finishCapture(true);}

function updateManualHomeControls(){
  const reason=window.CobotRltHome.manualReason({capture:current,console:consoleStatus,session:rltSession,deployment:window.CobotDeploymentUI?.state});
  window.CobotCaptureHome.setBusy(captureHomeBusy||manualHomeBusy||rltOperationBusy);
  window.CobotRltHome.setBusy(rltOperationBusy||manualHomeBusy);
  window.CobotCaptureHome.setManualBlocked(reason);
  window.CobotRltHome.setManualBlocked(reason);
}
async function readManualHomeState(){
  const [status,capture,deployment]=await Promise.all([request('/api/console/status'),request('/api/segmented-teach/status'),request('/api/deployment/status')]);
  const session=status.rlt_backend_phase==='offline'?null:await request('/api/rlt/session');
  return {console:status,capture,deployment,session};
}
async function homeCollection(kind){
  if(manualHomeBusy||captureHomeBusy||rltOperationBusy)return;
  const selection=window.CobotCaptureHome.selection();
  const show=kind==='rlt'?rltMessage:message;
  manualHomeBusy=true;updateButtons();renderConsole();
  try{
    await window.CobotRltHome.runManual({selection,readState:readManualHomeState,
      readDevices:()=>request('/api/console/devices'),
      startHome:spec=>window.CobotDeviceUI.execute(spec,`归位到 ${selection.pose}`,true),
      waitHome:waitForCaptureHome,
      onPhase:phase=>{const text=phase==='complete'?`归位完成：${selection.pose}`:`正在归位到 ${selection.pose}…`;show(text,false);window.CobotWorkspaceUI?.report(text,phase==='complete'?'success':'running','归位');},
    });
  }catch(error){show(error.message,true);window.CobotWorkspaceUI?.report(error.message,'error','归位');}
  finally{manualHomeBusy=false;updateButtons();await refreshConsole();}
}

function captureShortcutBlocked(event) {
  if (window.CobotUnifiedCollection?.changing) return true;
  if (event.defaultPrevented || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return true;
  const target = event.target;
  return Boolean(target && (target.isContentEditable || ["INPUT", "SELECT", "TEXTAREA", "VIDEO"].includes(target.tagName)));
}
async function handleCaptureShortcut(event) {
  if (captureShortcutBlocked(event) || activePageName() !== "operation" ||
      (consoleStatus && consoleStatus.selected_mode === "rlt") || captureShortcutBusy || captureHomeBusy || manualHomeBusy) return;
  const action=window.CobotUnifiedCollection.shortcutAction(event.key,{
    start:!$('#start').disabled,pause:!$('#pause').disabled,resume:!$('#resume').disabled,
    discard:!$('#discard').disabled,save:!$('#stop').disabled,
    paused:current.capture_state==='paused'&&!current.collection_model?.running&&!Object.values(current.teach_mask||{}).some(Boolean),
    results:window.CobotUnifiedCollection.labelResults(),success:!$('#capture-success').disabled,failure:!$('#capture-failure').disabled,
  });
  if (!action) { if(event.key===' ')event.preventDefault();return; }
  event.preventDefault(); captureShortcutBusy = true;
  try {
    if (action === "start") await start();
    else if(action==='discard')await finishCapture(true,null,true);
    else if(action==='save')await finishCapture(false,null,true);
    else if(['success','failure'].includes(action))await finishCapture(false,action,true);
    else await operate(action);
  } finally { captureShortcutBusy = false; }
}

async function handleRltShortcut(event) {
  if (captureShortcutBlocked(event) || activePageName() !== "operation" ||
      !consoleStatus || consoleStatus.selected_mode !== "rlt" || !rltSession || rltShortcutBusy || rltOperationBusy || manualHomeBusy) return;
  const buttons=rltButtons(consoleStatus,rltSession);
  const action=window.CobotUnifiedCollection.shortcutAction(event.key,{...buttons,discard:buttons.abort,
    paused:rltSession.policy_paused&&rltSession.phase!=='hil',results:window.CobotUnifiedCollection.labelResults()});
  if (!action) { if(event.key===' ')event.preventDefault();return; }
  event.preventDefault();
  rltShortcutBusy = true;
  try { await operateRlt(action==='discard'?'abort':action,{forceHome:['discard','save','success','failure'].includes(action)}); }
  finally { rltShortcutBusy = false; }
}

function showNode(node) {
  if (!viewedEpisode.episode_uuid) return;
  $("#node-preview").classList.add("hidden");
  let root=$('#episode-endpoints');
  if(!root || root.dataset.episodeUuid!==viewedEpisode.episode_uuid){showEpisodeEndpoints(viewedEpisode,viewedEpisode.nodes||[]);root=$('#episode-endpoints');}
  const key=historyGalleryKey(node);
  let item=[...root.children].find(card=>card.dataset.nodeKey===key);
  if(!item){
    item=createHistoryNodeCard(viewedEpisode,node,historyNodeLabel(node));
    const next=[...root.children].find(card=>Number(card.dataset.frameIndex)>Number(node.frame_index||0));
    root.insertBefore(item,next||null);
  }
  root.dataset.selectedNode=key;
  for(const card of root.children){const selected=card===item;card.classList.toggle('is-selected',selected);card.setAttribute('aria-current',String(selected));}
  for(const button of $('#timeline').querySelectorAll('.node-button')){const selected=button.dataset.nodeKey===key;button.classList.toggle('is-selected',selected);button.setAttribute('aria-pressed',String(selected));}
}

function historyGalleryKey(node){
  if(historyIsRlt())return node.endpoint||'frame:'+Math.max(0,Math.trunc(Number(node.frame_index||0)));
  return 'node:'+node.node_id;
}
function createHistoryNodeCard(episode,node,label){
  const item=document.createElement('div');item.className='episode-endpoint';item.dataset.nodeKey=historyGalleryKey(node);item.dataset.frameIndex=String(node.frame_index||0);
  item.setAttribute('role','group');item.setAttribute('aria-label',label);
  const title=document.createElement('strong');title.textContent=label;item.append(title);
  const grid=document.createElement('div');grid.className='camera-grid';
  for(const [camera,cameraLabel] of [['camera_left','左臂'],['camera_high','中臂'],['camera_right','右臂']]){
    const figure=document.createElement('figure'),image=document.createElement('img'),caption=document.createElement('figcaption');
    caption.textContent=cameraLabel;image.alt=label+' · '+cameraLabel;image.loading='eager';image.decoding='sync';
    image.addEventListener('error',()=>{caption.textContent=cameraLabel+' · 画面不可用';},{once:true});
    const base=historyIsRlt()?`/episodes/${episode.episode_uuid}/frames/${Math.max(0,Math.trunc(Number(node.frame_index||0)))}/${camera}.jpg`:
      node.node_id!=null?`/episodes/${episode.episode_uuid}/nodes/${node.node_id}/${camera}.jpg?g=${episode.generation}`:null;
    if(base)image.src=cachedFrameSource(historyEndpoint(base));
    figure.append(image,caption);grid.append(figure);
  }
  item.append(grid);return item;
}
function showEpisodeEndpoints(episode, normalNodes=null) {
  const uuid=episode&&episode.episode_uuid;
  if(!uuid)return;
  let root=$('#episode-endpoints');
  if(!root){
    root=document.createElement('div');root.id='episode-endpoints';root.className='episode-endpoints';
    $('#node-preview').before(root);
  }
  root.replaceChildren();root.dataset.episodeUuid=uuid;delete root.dataset.selectedNode;
  $("#node-preview").classList.add("hidden");
  const replay=$('#replay-panel');if(replay && replay.previousElementSibling!==root)root.after(replay);
  const nodes=historyIsRlt()?rltHistoryNodes({},episode.frame_count||episode.training_frame_count):
    [normalNodes&&normalNodes[0]||{},normalNodes&&normalNodes[normalNodes.length-1]||{}];
  for(let index=0;index<2;index++)root.append(createHistoryNodeCard(episode,nodes[index],index?'结束':'开始'));
}

function videoAfterNodeFrames(token) {
  const images=[...document.querySelectorAll('#episode-endpoints img')];
  Promise.all(images.map(image=>image.complete?Promise.resolve():new Promise(resolve=>{
    const finish=()=>resolve();image.addEventListener('load',finish,{once:true});image.addEventListener('error',finish,{once:true});setTimeout(finish,4000);
  }))).then(()=>{if(token===selectionToken)loadReplay();});
}

function renderTimeline() {
  window.CobotFeaturePaths?.update("history",{rlt:historyIsRlt(),dataRoot:historyDataRoot(),episode:viewedEpisode});
  const root = $("#timeline");
  root.replaceChildren();
  root.style.setProperty('--node-columns',Math.min(3,Math.max(1,(viewedEpisode.nodes||[]).length)));
  for (const node of viewedEpisode.nodes || []) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "node-button";
    button.dataset.nodeKey=historyGalleryKey(node);
    const selected=$('#episode-endpoints')?.dataset.selectedNode===button.dataset.nodeKey;
    button.classList.toggle('is-selected',selected);button.setAttribute('aria-pressed',String(selected));
    button.textContent = historyNodeLabel(node);
    button.addEventListener("click", () => showNode(node));
    item.append(button);
    root.append(item);
  }
}

function render(status) {
  current = status;
  window.CobotFeaturePaths?.update("normal",{dataRoot:selectedDataRoot()});
  $("#capture-state").textContent = status.capture_state || "unknown";
  $("#frame-count").textContent = String(status.training_frame_count || 0);
  $("#node-count").textContent = String(status.node_count || 0);
  $("#generation").textContent = String(status.generation || 0);
  updateButtons();
  if (!historyUuid && `${status.episode_uuid}:${status.generation}` !== renderedGeneration) {
    renderedGeneration = `${status.episode_uuid}:${status.generation}`;
    viewedEpisode = status;
    renderTimeline();
  }
}

async function refresh() {
  try {
    render(await request("/api/segmented-teach/status"));
  } catch (error) {
    message(`服务不可用：${error.message}`, true);
  }
}

function historyPageUrl(limit, offset) {
  if (historyIsRlt()) return historyEndpoint("/episodes");
  return historyEndpoint("/episodes")
    + `&limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`;
}

function historyEpisodeOutcome(episode) {
  const summary = rltEpisodeSummaryByUuid.get(episode.episode_uuid) || {};
  return String(episode.episode_outcome || summary.outcome || "unknown").toLowerCase();
}

function visibleHistory(episodes) {
  if (!historyIsRlt()) return episodes;
  return episodes.filter(episode => historyEpisodeOutcome(episode) !== "aborted");
}

function terminalNodeFor(episode) {
  const last = Math.max(0, Math.trunc(Number(episode.frame_count || episode.training_frame_count || 1)) - 1);
  return {
    frame_index: last,
    node_kind: "summary",
    node_id: 1,
    display_label: `结束 · frame ${last}`,
    sample_timestamp: last / 30,
  };
}

function appendHistoryOptions(episodes, replace = false) {
  const select = $("#episode-history");
  const previous = select.value;
  if (replace) select.replaceChildren();
  const existing = new Set([...(select.options || select.children || [])].map(option => option.value));
  for (const episode of episodes) {
    if (existing.has(episode.episode_uuid)) continue;
    const option = document.createElement("option");
    option.value = episode.episode_uuid;
    const index = episode.episode_index == null ? "—" : episode.episode_index;
    const frames = episode.training_frame_count == null ? Number(episode.frame_count || 0) : Number(episode.training_frame_count || 0);
    const summary = rltEpisodeSummaryByUuid.get(episode.episode_uuid) || {};
    const outcome = episode.episode_outcome || summary.outcome;
    const kind = (summary.hil || episode.has_hil) ? "HIL" : (historyIsRlt() ? "自主" : `${Number(episode.node_count || 0)} nodes`);
    const state = episode.commit_state || episode.completion_state || "complete";
    option.textContent = `episode ${index} · ${!outcome||outcome==='unknown'?"未标注":outcome} · ${kind} · ${frames} frames · ${state}`;
    select.append(option);
  }
  if (episodes.some(e => e.episode_uuid === previous)) select.value = previous;
  updateButtons();
  updateEpisodeBrowserControls();
}

async function loadRemainingHistory(token, offset) {
  if (historyIsRlt()) return;
  let loaded = offset;
  try {
    while (token === historyRefreshToken) {
      const page = await request(historyPageUrl(HISTORY_BACKGROUND_PAGE, loaded));
      if (token !== historyRefreshToken) return;
      historyEpisodes.push(...page);
      appendHistoryOptions(page, false);
      loaded += page.length;
      $("#history-load-state").textContent = `已加载 ${loaded} 条`;
      if (page.length < HISTORY_BACKGROUND_PAGE) break;
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    if (token === historyRefreshToken) {
      $("#history-load-state").textContent = `${loaded} 条记录 · 已完成`;
      $("#history-load-state").classList.remove("is-loading");
    }
  } catch (error) {
    if (token === historyRefreshToken) {
      $("#history-load-state").textContent = `已加载 ${loaded} 条 · 较早记录稍后重试`;
      $("#history-load-state").classList.remove("is-loading");
    }
  }
}

async function refreshHistory(options = {}) {
  const token = ++historyRefreshToken;
  mediaPreloadToken++;
  const loadSelected = Boolean(options.loadSelected);
  const state = $("#history-load-state");
  state.textContent = "正在加载最近记录";
  state.classList.add("is-loading");
  let episodes = await request(historyPageUrl(HISTORY_INITIAL_PAGE, 0));
  const hiddenAborted = historyIsRlt()
    ? episodes.filter(episode => historyEpisodeOutcome(episode) === "aborted").length : 0;
  episodes = visibleHistory(episodes);
  if (historyIsRlt()) episodes = [...episodes].sort((a, b) => Number(b.episode_index || 0) - Number(a.episode_index || 0));
  if (token !== historyRefreshToken) return 0;
  historyEpisodes = episodes.slice();
  if(!episodes.length)window.CobotFeaturePaths?.update("history",{rlt:historyIsRlt(),dataRoot:historyDataRoot()});
  appendHistoryOptions(episodes, true);
  const hiddenText = hiddenAborted ? ` · 已隐藏 ${hiddenAborted} 条放弃` : "";
  state.textContent = episodes.length ? `可用 ${episodes.length} 条${hiddenText}` : `当前目录暂无可用记录${hiddenText}`;
  if (historyIsRlt()) {
    rltHistoryCount = episodes.length;
    $("#rlt-saved-count").textContent = String(episodes.length);
  }
  if (loadSelected && episodes.length) await loadHistory();
  if (episodes.length) preloadEpisodeMedia(++mediaPreloadToken, episodes, historyIsRlt());
  if (!historyIsRlt() && episodes.length === HISTORY_INITIAL_PAGE) {
    setTimeout(() => loadRemainingHistory(token, episodes.length), 0);
  } else {
    state.textContent = `${episodes.length} 条可用记录${hiddenText} · 已完成`;
    state.classList.remove("is-loading");
  }
  return episodes.length;
}

async function preloadEpisodeMedia(token, episodes, isRlt) {
  const state = $("#history-load-state");
  const active = () => token === mediaPreloadToken && historyIsRlt()===isRlt;
  let framesReady = 0;
  let nextFrame = 0;
  const loadFrame = async () => {
    while (active() && nextFrame < episodes.length) {
      const episode = episodes[nextFrame++];
      try {
        let bases=[];
        if(isRlt){
          const last=Math.max(0,Number(episode.frame_count||episode.training_frame_count||1)-1);
          bases=[...new Set([0,Math.trunc(last)])].map(frame=>`/episodes/${episode.episode_uuid}/frames/${frame}`);
        }else{
          const detailKey=episodeDetailKey(episode.episode_uuid);
          const detail=preloadedEpisodeDetails.get(detailKey) || await request(historyEndpoint(`/episodes/${episode.episode_uuid}`));
          preloadedEpisodeDetails.set(detailKey,detail);
          const nodes=detail.nodes||[];
          bases=[nodes[0],nodes[nodes.length-1]].filter(Boolean).map(node=>`/episodes/${episode.episode_uuid}/nodes/${node.node_id}`);
          episode.generation=detail.generation||episode.generation;
        }
        for (const base of bases) {
          const urls = ["camera_high", "camera_left", "camera_right"].map(camera =>
            historyEndpoint(`${base}/${camera}.jpg${isRlt?'':'?g='+episode.generation}`));
          await Promise.all(urls.map(url => preloadedFrames.has(url)?Promise.resolve():fetch(url, {cache:"force-cache"}).then(async response => {
            if (!response.ok) throw new Error(`frame HTTP ${response.status}`);
            const blob=await response.blob();
            if(active())rememberFrame(url,blob);
          })));
        }
        framesReady++;
      } catch (_error) { /* The selected episode can retry a missing frame. */ }
      if (active()) state.textContent = `节点帧 ${framesReady}/${episodes.length} · 视频待加载`;
    }
  };
  await Promise.all(Array.from({length:Math.min(4,episodes.length)}, loadFrame));
  let videosReady = 0;
  let nextVideo = 0;
  const loadVideo = async () => {
    while (active() && nextVideo < episodes.length) {
      const episode = episodes[nextVideo++];
      const uuid = episode.episode_uuid;
      if (preloadedVideos.has(uuid)) { videosReady++; continue; }
      try {
        let preview = null;
        for (let attempt = 0; attempt < 30 && active(); attempt++) {
          preview = await request(historyEndpoint(isRlt?`/episodes/${uuid}/preview/status`:`/episodes/${uuid}/preview`));
          if (preview.state === "ready" || preview.state === "error") break;
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
        if (preview && preview.state === "ready" && active()) {
          const response = await fetch(historyEndpoint(`/episodes/${uuid}/preview.mp4`));
          if (response.ok && active()) {
            preloadedVideos.set(uuid, {url:URL.createObjectURL(await response.blob()),state:preview});
            videosReady++;
          }
        }
      } catch (_error) { /* A failed preview remains available for explicit retry. */ }
      if (active()) state.textContent = `节点帧 ${framesReady}/${episodes.length} · 视频 ${videosReady}/${episodes.length}`;
    }
  };
  if (active()) await Promise.all(Array.from({length:Math.min(2,episodes.length)}, loadVideo));
  if (active()) state.textContent = `节点帧 ${framesReady}/${episodes.length} · 视频 ${videosReady}/${episodes.length} · 已完成`;
}

function resetReplay(preserveHistoryLayout=false) {
  clearTimeout(replayTimer);
  const endpoints=$('#episode-endpoints');if(endpoints&&!preserveHistoryLayout)endpoints.remove();
  const video = $("#episode-video");
  video.pause(); video.removeAttribute("src"); video.load();
  $("#replay-panel").classList.toggle("hidden",!preserveHistoryLayout);
  if(preserveHistoryLayout)$("#replay-status").textContent="";
  $("#episode-inspection").classList.add("hidden");
  $("#episode-contact-sheet").removeAttribute("src");
  $("#episode-qpos").removeAttribute("src");
  if (replayTimeline) replayTimeline.setEpisode([], {});
}
function showLive() {
  historyUuid = null; selectionToken++; currentReview = null;
  viewedEpisode = current; renderedGeneration = -1; resetReplay();
  $("#review-panel").classList.add("hidden");
  $("#node-preview").classList.add("hidden");
  render(current);
}
async function loadHistory(showFirstNode = true) {
  const episodeUuid = $("#episode-history").value;
  if (!episodeUuid) return;
  const token = ++selectionToken;
  historyUuid = episodeUuid; currentReview = null; resetReplay(true);
  window.CobotFeaturePaths?.update("history",{rlt:historyIsRlt(),dataRoot:historyDataRoot(),episode:historyEpisodes.find(e=>e.episode_uuid===episodeUuid)});
  $("#timeline").replaceChildren();
  $("#node-preview").classList.add("hidden");
  const cachedDetail=preloadedEpisodeDetails.get(episodeDetailKey(episodeUuid));
  if(!historyIsRlt()&&!cachedDetail)showEpisodeEndpoints({episode_uuid:episodeUuid,generation:0},[]);
  $("#review-panel").classList.add("hidden");
  if (historyIsRlt()) {
    const metadata = historyEpisodes.find(item => item.episode_uuid === episodeUuid) || {};
    showEpisodeEndpoints({...metadata,episode_uuid:episodeUuid});
    const cachedLabels=preloadedRltLabels.get(episodeDetailKey(episodeUuid));
    viewedEpisode = {...metadata, episode_uuid: episodeUuid, nodes: rltHistoryNodes(cachedLabels||{},metadata.frame_count)};
    renderTimeline();
    if (showFirstNode) videoAfterNodeFrames(token);
    const labels = await request(historyEndpoint(`/episodes/${episodeUuid}/labels`));
    if (token !== selectionToken) return;
    preloadedRltLabels.set(episodeDetailKey(episodeUuid),labels);
    while(preloadedRltLabels.size>300)preloadedRltLabels.delete(preloadedRltLabels.keys().next().value);
    const nodes = rltHistoryNodes(labels, metadata.frame_count);
    const hilNodes = nodes.filter(node => node.node_kind === "hil");
    viewedEpisode = {...metadata, ...labels, episode_uuid:episodeUuid, nodes};
    renderTimeline();
    if (replayTimeline) replayTimeline.setEpisode(nodes, {source_fps: 30, source_frame_count: metadata.frame_count || 0});
    $("#review-panel").classList.remove("hidden");
    $("#review-intervals").replaceChildren(...hilNodes.map(node => {
      const row = document.createElement("div"); row.className = "review-option"; row.textContent = historyNodeLabel(node); return row;
    }));
    $("#review-note").value = labels.operator_note || "";
    $("#review-revision").textContent = `${labels.episode_outcome || "unknown"} · ${hilNodes.length ? hilNodes.length + " 个 HIL 区间" : "纯自主 rollout"}`;
    const selected = $("#episode-history").selectedOptions[0];
    if (selected) selected.textContent = selected.textContent.replace(" · 自主 ·", hilNodes.length ? " · HIL ·" : " · 自主 ·");
    // The two default node frames were already requested before label metadata.
    return;
  }
  const payload = cachedDetail || await request(historyEndpoint(`/episodes/${episodeUuid}`));
  preloadedEpisodeDetails.set(episodeDetailKey(episodeUuid),payload);
  if (token !== selectionToken) return;
  showEpisodeEndpoints(payload,payload.nodes||[]);
  viewedEpisode = payload; renderTimeline();
  if (replayTimeline) replayTimeline.setEpisode(payload.nodes || [], {});
  if (showFirstNode && (payload.nodes || []).length) videoAfterNodeFrames(token);
  const review = await request(historyEndpoint(`/episodes/${episodeUuid}/review`));
  if (token !== selectionToken) return;
  currentReview = review; renderReview(review);
}
async function loadReplay() {
  const uuid = historyUuid;
  if (!uuid || viewedEpisode.episode_uuid !== uuid) return;
  const token = selectionToken;
  $("#node-preview").classList.add("hidden");
  $("#replay-panel").classList.remove("hidden");
  $("#replay-status").textContent = "正在准备回放…";
  try {
    const cached = preloadedVideos.get(uuid);
    const state = cached ? cached.state : await request(historyIsRlt()
      ? historyEndpoint(`/episodes/${uuid}/preview/status`)
      : historyEndpoint(`/episodes/${uuid}/preview`));
    if (token !== selectionToken) return;
    if (state.state === "ready") {
      const video = $("#episode-video");
      video.src = cached ? cached.url : historyEndpoint(`/episodes/${uuid}/preview.mp4`);
      video.load();
      if (replayTimeline) replayTimeline.setEpisode(viewedEpisode.nodes || [], state);
      $("#replay-status").textContent = "回放已就绪；拖动进度条，靠近节点会自动吸附。";
    } else if (state.state === "error") {
      $("#replay-status").textContent = "回放生成失败，请查看服务日志。";
    } else {
      $("#replay-status").textContent = `回放准备中：${state.completed_frames || 0}/${state.total_frames || 0} 帧`;
      replayTimer = setTimeout(loadReplay, 1000);
    }
  } catch (error) { if (token === selectionToken) historyMessage(error.message, true); }
}

function renderReview(review) {
  const panel = $("#review-panel");
  const root = $("#review-intervals");
  root.replaceChildren();
  const selected = new Set(review.selected_interval_ids || []);
  const intervals = review.intervals || intervalsFromNodes(viewedEpisode.nodes || []);
  for (const interval of intervals) {
    const label = document.createElement("label");
    label.className = "review-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.dataset.intervalId = String(interval.interval_id);
    checkbox.checked = selected.has(interval.interval_id);
    const secondsStart = Number(interval.start_frame) / 30;
    const secondsEnd = Number(interval.end_frame_exclusive) / 30;
    label.append(checkbox, document.createTextNode(
      `区间 ${interval.interval_id}：节点 ${interval.start_node_id} → ${interval.end_node_id} · ${secondsStart.toFixed(2)}–${secondsEnd.toFixed(2)}s · ${interval.end_frame_exclusive - interval.start_frame} frames`,
    ));
    root.append(label);
  }
  $("#review-note").value = review.note || "";
  $("#review-revision").textContent = `当前审核版本：${review.review_revision}`;
  panel.classList.remove("hidden");
}

async function saveReview() {
  if (!currentReview || !historyUuid || currentReview.episode_uuid !== historyUuid) return;
  const uuid = historyUuid;
  const token = selectionToken;
  const selected = [...$$("#review-intervals input:checked")]
    .map((input) => Number(input.dataset.intervalId));
  try {
    const savedReview = await request(
      historyEndpoint(`/episodes/${uuid}/review`),
      {
        method: "PUT",
        body: JSON.stringify({
          expected_revision: Number(currentReview.review_revision),
          selected_interval_ids: selected,
          note: $("#review-note").value.trim() || null,
        }),
      },
    );
    if (token !== selectionToken || historyUuid !== uuid) return;
    currentReview = savedReview;
    renderReview(currentReview);
    message(`训练区间审核已保存为 revision ${currentReview.review_revision}。`, false);
  } catch (error) {
    message(error.message, true);
  }
}

async function start() {
  try {
    showLive();
    currentReview = null;
    $("#review-panel").classList.add("hidden");
    render(await request("/api/segmented-teach/start", {
      method: "POST",
      body: JSON.stringify(startPayload()),
    }));
    saveConfig();
    message("episode 已开始；示教退出/进入会自动暂停/继续并打节点。", false);
  } catch (error) {
    message(error.message, true);
  }
}

async function prepareStorage(silent = false) {
  const requestedRoot = selectedDataRoot();
  setButtonBusy($("#prepare-storage"), true, "正在检查");
  try {
    const prepared = await request("/api/segmented-teach/storage/prepare", {
      method: "POST",
      body: JSON.stringify(startPayload()),
    });
    if (requestedRoot !== selectedDataRoot()) return;
    storagePrepared = true;
    $("#episode-directory").textContent = prepared.episode_directory;
    $("#next-episode").textContent = `episode_${String(prepared.next_episode_index).padStart(6, "0")}`;
    saveConfig();
    if (normalPathPicker) normalPathPicker.remember(prepared.data_root);
    updateButtons();
    await refreshHistory({loadSelected: true});
    if (!silent) message("数据目录已核验；不存在时已安全创建。", false);
  } catch (error) {
    if (requestedRoot !== selectedDataRoot()) return;
    showLive();
    storagePrepared = false;
    $("#episode-directory").textContent = "检查失败";
    $("#next-episode").textContent = "—";
    updateButtons();
    message(error.message, true);
  } finally {
    setButtonBusy($("#prepare-storage"), false);
  }
}

async function deleteHistory() {
  const episodeUuid = $("#episode-history").value;
  if (!episodeUuid) return;
  mediaPreloadToken++;
  const select = $("#episode-history");
  const oldIndex = select.selectedIndex;
  const selectedText = select.selectedOptions[0] ? select.selectedOptions[0].textContent : episodeUuid.slice(0, 8);
  const replayNote = historyIsRlt() ? "\n已提交到 replay 的 transition 不会被回滚。" : "";
  if (!window.confirm(window.CobotPreferences.text(`永久删除 ${selectedText}？${replayNote}\n按 Enter / OK 确认。`))) return;
  try {
    const deleted = await request(historyEndpoint(`/episodes/${episodeUuid}`), {
      method: "DELETE",
      ...(historyIsRlt() ? {body: JSON.stringify({episode_uuid: episodeUuid})} : {}),
    });
    historyUuid = null; selectionToken++; resetReplay();
    const cached=preloadedVideos.get(episodeUuid);
    if(cached){URL.revokeObjectURL(cached.url);preloadedVideos.delete(episodeUuid);}
    currentReview = null;
    $("#review-panel").classList.add("hidden");
    $("#node-preview").classList.add("hidden");
    historyMessage("所选记录已删除。", false);
    if (historyIsRlt()) {
      await refreshHistory({loadSelected:false});
      if (select.options.length) {
        select.selectedIndex = Math.min(Math.max(0, oldIndex), select.options.length - 1);
        await loadHistory();
      } else showLive();
    } else await prepareStorage(true);
  } catch (error) {
    historyMessage(error.message, true);
  }
}

function historyShortcutSafe() {
  if (!$("#episode-history").options.length) return false;
  if (!historyIsRlt()) return !["recording", "paused", "finalizing"].includes(current.capture_state);
  const phase = String((rltSession && rltSession.phase) || "offline");
  return !["recording_starting", "rollout", "hil", "paused", "terminal_pending", "finalizing", "replay_committing"].includes(phase);
}

async function moveHistorySelection(delta) {
  const select = $("#episode-history");
  if (!select.options.length) return;
  const currentIndex = select.selectedIndex < 0 ? 0 : select.selectedIndex;
  select.selectedIndex = Math.max(0, Math.min(select.options.length - 1, currentIndex + delta));
  await loadHistory();
}

async function handleEpisodeHistoryShortcut(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  const target = event.target;
  if (target && target.isContentEditable) return;
  if (target && ["INPUT", "TEXTAREA", "VIDEO"].includes(target.tagName)) return;
  if (!historyShortcutSafe() && !["Delete", "Backspace"].includes(event.key)) return;
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    await moveHistorySelection(event.key === "ArrowUp" ? -1 : 1);
  } else if ((event.key === "Delete" || event.key === "Backspace") && !$("#delete-history").disabled) {
    event.preventDefault();
    await withButtonBusy($("#delete-history"), deleteHistory, "删除中");
  }
}

async function operate(name) {
  try {
    if (name === "stop") {
      await finalizeAndHome();
      return;
    }
    if (name === "discard") {
      await discardAndHome();
      return;
    }
    render(await request(`/api/segmented-teach/${name}`, {
      method: "POST",
      body: JSON.stringify(versionRequest(current)),
    }));
    message(`${name} 已完成。`, false);
  } catch (error) {
    message(error.message, true);
    await refresh();
  }
}

function compactEvidence(payload) {
  const learning = payload.learning || {};
  const cycle = learning.cycle || {};
  const operation = learning.operation || {};
  const release = learning.release || {};
  const session = payload.session || {};
  const robot = payload.robot || {};
  return {
    availability: payload.availability,
    cycle: {
      phase: cycle.phase, pending_episodes: cycle.pending_episodes,
      episodes_per_update: cycle.episodes_per_update,
      quarantined_episodes: cycle.quarantined_episodes,
      new_uuids: cycle.new_uuids,
    },
    latest_candidate: {
      phase: operation.phase, accepted: operation.accepted,
      actor_version: operation.actor_version, updates: operation.updates,
      new_transitions: operation.new_transitions, reasons: operation.reasons,
    },
    release: {
      name: release.name, actor_version: release.actor_version,
      actor_updates: release.actor_updates, status: release.status,
      onsite_validated: release.onsite_validated,
      online_improvement_validated: release.online_improvement_validated,
    },
    session: {
      phase: session.phase, policy_paused: session.policy_paused,
      chunk_count: session.chunk_count,
      last_inference_latency_sec: session.last_inference_latency_sec,
      fault_reason: session.fault_reason,
    },
    robot_summary: robot.summary,
  };
}
function renderDiagnostics(payload) {
  const ui = window.CobotDiagnosticsUI;
  const learning = payload.learning || {};
  if (payload.parameters) renderCoreParameters(payload.parameters);
  const cycle = learning.cycle || {};
  const operation = learning.operation || {};
  const release = learning.release || {};
  const metrics = learning.metrics || [];
  if (activePageName() === 'training') {
    const live = $('#training-live-state');
    const latest = metrics.length ? metrics[metrics.length - 1] : null;
    live.textContent = latest && Number.isFinite(Number(latest.global_step))
      ? `在线指标：step ${latest.global_step} · ${metrics.length} 个采样点`
      : '当前没有在线 learner 指标；下方是已完成的离线 warmup 诊断。';
    ui.renderChart($('#training-live-loss'), metrics,
      [{key:'critic_loss',label:'Critic loss',unit:'loss',color:'#65d8e8'}],
      {xLabel:'训练 step',yLabel:'loss',logY:true});
    ui.renderChart($('#training-live-q'), metrics,
      [{key:'q1_mean',label:'Q1',unit:'Q',color:'#65d8e8'},
       {key:'q2_mean',label:'Q2',unit:'Q',color:'#ad91ff'},
       {key:'target_q_mean',label:'Target Q',unit:'Q',color:'#f0bd67'}],
      {xLabel:'训练 step',yLabel:'Q'});
    ui.renderChart($('#training-live-actor'), metrics.filter(ui.actorUpdated),
      [{key:'actor_loss',label:'Actor loss',unit:'loss',color:'#ff8178'},
       {key:'weighted_bc',label:'BC',unit:'loss',color:'#65d8e8'},
       {key:'weighted_q',label:'Q',unit:'loss',color:'#ad91ff'}],
      {xLabel:'训练 step',yLabel:'weighted loss',emptyText:'暂无 actor update'});
  }
  const explanation = ui.formatExplanation(learning.update_explanation);
  $("#diag-update-card").dataset.tone = explanation.tone;
  $("#diag-update-title").textContent = explanation.title;
  $("#diag-update-detail").textContent = explanation.detail;
  const pending = Number(cycle.pending_episodes || 0);
  const required = Number(cycle.episodes_per_update || 5);
  $("#diag-update-progress").style.width = Math.min(100, pending / Math.max(1, required) * 100) + "%";
  $("#diag-pending").textContent = pending + " / " + required;
  $("#diag-quarantined").textContent = String(cycle.quarantined_episodes || 0);
  $("#diag-actor").textContent = release.actor_version == null ? "—" : String(release.actor_version);
  $("#diag-release-name").textContent = release.name || "release —";
  $("#diag-last-result").textContent = operation.phase || "—";

  const reasons = $("#diag-update-reasons");
  reasons.replaceChildren();
  const evidence = [];
  if (operation.phase === "accepted") {
    evidence.push("最近候选已接受：" + (operation.new_transitions || 0) + " transitions / " + (operation.updates || 0) + " updates。");
    evidence.push("离线发布门限全部通过；下一次 Session 才会加载新 actor。");
  } else if (operation.phase === "rejected") {
    evidence.push("最近候选未发布；这不会阻塞由全新 UUID 组成的下一批。");
    for (const reason of operation.reasons || []) evidence.push("拒绝原因：" + reason);
  } else {
    evidence.push("最近候选状态：" + (operation.phase || "暂无"));
  }
  if (release.onsite_validated === false) evidence.push("当前 release 尚未标记为现场验证。");
  for (const text of evidence) {
    const item = document.createElement("li"); item.textContent = text; reasons.append(item);
  }

  // Release boundaries of the deployed lineage: hover a dashed line to see which release starts there.
  const markers=((learning.history||{}).releases||[]).map(r=>({x:r.global_step,label:r.name}));
  // Critic loss spans several decades (a critic reset jumps it back up); linear axes flatten everything else.
  ui.renderChart($("#chart-loss"), metrics, [
    {key:"critic_loss",label:"Critic",unit:"loss",color:"#65d8e8"},
  ], {xLabel:"训练 step",yLabel:"loss",logY:true,markers});
  // Actor columns are zero-filled on critic-only log windows; plot them only where the actor was updated.
  ui.renderChart($("#chart-q"), metrics, [
    {key:"q1_mean",label:"Q1",unit:"Q",color:"#65d8e8"}, {key:"q2_mean",label:"Q2",unit:"Q",color:"#ad91ff"},
    {key:"target_q_mean",label:"Target",unit:"Q",color:"#f0bd67"},
    {key:"actor_q",label:"Q(actor)",unit:"Q",color:"#66e0ab",when:ui.actorUpdated},
  ], {xLabel:"训练 step",yLabel:"Q",markers});
  const actorRows=metrics.filter(ui.actorUpdated);
  ui.renderChart($("#chart-actor-objective"), actorRows, [
    {key:"actor_loss",label:"actor loss",unit:"loss",color:"#ff8178"},
    {key:"weighted_bc",label:"weighted BC",unit:"loss",color:"#65d8e8"},
    {key:"weighted_q",label:"weighted Q",unit:"loss",color:"#ad91ff"},
    {key:"weighted_delta",label:"weighted delta",unit:"loss",color:"#f0bd67"},
  ], {xLabel:"训练 step（仅 actor update）",yLabel:"weighted loss",markers,emptyText:"日志尚未包含 actor update"});
  const last = metrics.length ? metrics[metrics.length - 1] : null;
  $("#diag-actor-note").textContent = ui.actorUpdateNote(last);
  // Two partitions that each sum to 100% (outcome; data source), plus one overlapping share.
  // sample_human_intervention_ratio counts demonstrations as well, so it is shown as "人工", not "HIL".
  const success = last && Number.isFinite(Number(last.sample_success_ratio)) ? Number(last.sample_success_ratio) : null;
  ui.renderBars($("#diag-mix"), last ? [
    {label:"成功样本",value:success == null ? 0 : success},
    {label:"失败样本",value:success == null ? 0 : 1 - success},
    {label:"来源·人工（示教+纠正）",value:last.sample_source_human_ratio || 0},
    {label:"来源·RL 策略",value:last.sample_source_rl_ratio || 0},
    {label:"来源·Stage-1",value:last.sample_source_base_ratio || 0},
    {label:"其中近期在线",value:last.sample_recent_online_ratio || 0},
  ] : []);
  $("#diag-mix-note").textContent = last ? "训练 step " + Number(last.global_step).toFixed(0) + " 的一个 batch：成功+失败=100%，三种来源合计=100%；“近期在线”与其它项重叠。" : "暂无训练指标。";

  const episodeQ=learning.episode_q||{}; const qRows=[]; const qDefs=[];
  const colors={autonomous_success:"#66e0ab",autonomous_failure:"#ff8178",hil_success:"#f0bd67",hil_failure:"#ad91ff"};
  const groupNames={autonomous_success:"自主成功",autonomous_failure:"自主失败",hil_success:"人工纠正·成功",hil_failure:"人工纠正·失败"};
  // Curves are resampled to a fixed number of points per episode; show them against episode progress 0–1.
  for(const group of episodeQ.groups||[]){
    const key=String(group.name||'group'),median=group.median||[],last=Math.max(1,median.length-1);
    qDefs.push({key,label:(groupNames[key]||key)+' ('+(group.count||0)+' 条)',unit:'Q',color:colors[key]||'#65d8e8'});
    median.forEach((value,index)=>{while(qRows.length<=index)qRows.push({progress:qRows.length/last});qRows[index][key]=value;});
  }
  ui.renderChart($("#chart-episode-q"),qRows,qDefs,{xKey:'progress',xLabel:'episode 进度',yLabel:'Q (中位数)',xDigits:2,xFormat:v=>v.toFixed(1),emptyText:'等待 Q 位置评估'});
  ui.renderLegend($("#legend-episode-q"),qDefs);
  const qSource=episodeQ.source==='release'?'当前 release（step '+episodeQ.source_step+'）的留出评估':episodeQ.source==='latest_candidate'?'最近一次在线候选的评估，不一定是当前 release':'';
  $("#diag-q-separation").textContent=(qDefs.length?'每条线是该组留出 episode 的 Q 中位数；critic 有效时绿线（成功）应在红线（失败）上方。':'')+(episodeQ.autonomous_separation==null?'等待 autonomous 留出评估':'自主成功−失败 '+Number(episodeQ.autonomous_separation).toFixed(4)+' · AUC '+Number(episodeQ.auc||0).toFixed(3)+(qSource?' · 来源：'+qSource:''));
  const decisionRows=ui.latestRun(learning.decision_q||[]);const decisionActor=decisionRows.length?decisionRows[decisionRows.length-1].actor_version:null;
  const decisionDefs=[
    {key:'actor_q',label:'actor 动作',unit:'Q',color:'#66e0ab'},{key:'reference_q',label:'Stage-1 reference',unit:'Q',color:'#65d8e8'},{key:'executed_q',label:'实际执行',unit:'Q',color:'#f0bd67'}
  ];
  ui.renderChart($("#chart-decision-q"),decisionRows,decisionDefs,{xKey:'decision',xLabel:'decision chunk（最近一段连续推理）',yLabel:'Q',emptyText:'等待异步 critic telemetry'});
  // Only list series that have data, so the legend does not advertise an empty line.
  ui.renderLegend($("#legend-decision-q"),decisionDefs.filter(d=>decisionRows.some(r=>Number.isFinite(Number(r[d.key])))));
  const deployedActor=release.actor_version==null?null:Number(release.actor_version);
  const staleNote=v=>v==null?'':(deployedActor!==null&&Number(v)!==deployedActor?'actor '+v+'（不是当前部署的 '+deployedActor+'，当前 actor 还没有推理数据）':'actor '+v);
  $("#diag-decision-q-note").textContent='异步 critic 评估 actor/reference/executed，不阻塞控制。'+(decisionActor==null?'':' · '+staleNote(decisionActor));
  const latestDelta=(learning.actor_delta||[]).slice(-1)[0]||{}; const deltaRows=(latestDelta.horizon_rms||[]).map((value,index)=>({horizon:index,delta_rms:value}));
  ui.renderChart($("#chart-actor-delta"),deltaRows,[{key:'delta_rms',label:'conditioned delta',unit:'rad RMS',color:'#ad91ff'}],{xKey:'horizon',xLabel:'action horizon',yLabel:'rad RMS',emptyText:'等待 actor 修改量'});
  $("#diag-actor-delta-note").textContent='横轴为 horizon 0–9，纵轴为六关节 RMS；显示最近一次推理。'+(latestDelta.actor_version==null?'':' · '+staleNote(latestDelta.actor_version));
  const progress=learning.progress;
  if(progress){$("#diag-update-progress").style.width=(Number(progress.fraction||0)*100)+'%';$("#diag-update-detail").textContent=progress.message+' · '+progress.completed+'/'+progress.total;}

  const session = payload.session || {};
  $("#sys-console").textContent = "online";
  $("#sys-actor").textContent = release.actor_version == null ? "—" : String(release.actor_version);
  $("#sys-onsite").textContent = release.onsite_validated ? "yes" : "pending";
  $("#sys-improvement").textContent = release.online_improvement_validated ? "yes" : "pending";
  $("#sys-latency").textContent = session.last_inference_latency_sec == null
    ? "—" : (Number(session.last_inference_latency_sec) * 1000).toFixed(0) + " ms";
  $("#diag-system-json").textContent = JSON.stringify(compactEvidence(payload), null, 2);
}
function diagnosticsFailed(error) {
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report('诊断不可用：'+error.message,'error','诊断');
}
function deviceButtons(component) {
  const ids = {
    can: ["device-can", "device-can-reset"],
    roscore: ["device-roscore"],
    arms: ["device-arms"],
    cameras: ["device-cameras"],
    home: ["device-home", "device-pose-capture", "device-pose-delete"],
    pose: ["device-home", "device-pose-capture", "device-pose-delete"],
    recover: ["device-recover"],
    rlt: ["device-rlt-reference", "device-rlt-warmup", "device-rlt-frozen", "device-rlt-online"],
  };
  return (ids[component] || []).map(id => document.getElementById(id)).filter(Boolean);
}
function deviceJobBusy(component, job, system) {
  if (!job || !["running", "stopping"].includes(String(job.phase || ""))) return false;
  if (["roscore", "arms", "cameras", "rlt"].includes(component)) return false;
  return !(system && system.phase === "ready");
}
function deviceJobLine(job) {
  if (!job) return "暂无控制台进程日志。";
  const elapsed = Math.max(0, Number((job.finished_at || Date.now() / 1000) - Number(job.started_at || 0)));
  const heading = `${String(job.component || "job").toUpperCase()} · ${job.phase || "unknown"} · ${elapsed.toFixed(1)} s`;
  return heading + (job.log_tail ? `\n\n${job.log_tail}` : "\n\n进程已启动，等待首条日志…");
}
function renderDevices(payload){
  const jobs=payload.jobs||{}; const systems=payload.systems||{}; const ui=window.CobotDeviceUI;
  ui.updateLifecycleControls(payload);
  ui.setHomePoses(payload.home_poses||{});
  window.CobotRltHome.setCatalog(payload.home_poses||{});
  window.CobotCaptureHome.setCatalog(payload.home_poses||{});
  for(const name of ['can','roscore','arms','cameras','rlt']){
    const lifecycle=jobs[name]||null; const visible=systems[name]||lifecycle; const node=$('#dev-'+name);
    const names={can:'CAN',roscore:'ROS Core',arms:'机械臂节点',cameras:'相机节点',rlt:'RLT 后端'};
    const phases={ready:'正常',offline:'未启动',error:'异常',running:'启动中',stopping:'停止中',stopped:'已停止',failed:'失败',stale:'状态过期'};
    const details={can:'5/5 接口',roscore:'主控已连接',arms:'3/3 节点',cameras:'3/3 节点',rlt:'服务运行中'};
    node.dataset.tone=ui.tone(visible);
    node.textContent=names[name]+' · '+(phases[(visible||{}).phase]||'未核验')+
      ((visible||{}).phase==='ready'?' · '+details[name]:'');
    node.title=String((visible&&visible.detail)||'');
    if(lifecycle&&['completed','failed','stopped','stale'].includes(lifecycle.phase)){
      const marker=lifecycle.job_id+':'+lifecycle.phase;
      if(deviceCompletionSeen.get(name)!==marker){
        deviceCompletionSeen.set(name,marker);
        if(lifecycle.phase==='completed'){$('#device-message').textContent=`${name} 已完成，可继续操作。`;$('#device-message').classList.remove('error');}
        else if(lifecycle.phase==='failed'||lifecycle.phase==='stale'){$('#device-message').textContent=`${name} 未完成，请查看进程日志。`;$('#device-message').classList.add('error');}
        if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(
          `${names[name]} · ${phases[lifecycle.phase]||lifecycle.phase}`,
          lifecycle.phase==='failed'||lifecycle.phase==='stale'?'error':'success','设备');
      }
    }
  }
  const feedback=systems.arms_feedback||{};
  const labels={'front-left':'左前臂','front-right':'右前臂',mid:'中臂','rear-left':'左后臂','rear-right':'右后臂','gripper-left':'左夹爪','gripper-right':'右夹爪'};
  for(const key of Object.keys(labels)){
    const armKey=key.startsWith('gripper-')?'front-'+key.slice(8):key;
    const arm=feedback[armKey];
    const status=key.startsWith('gripper-')?(arm&&(arm.gripper||arm)):arm;
    const node=$('#dev-'+key);
    if(!node)continue;
    let phase=String((status&&status.phase)||'unknown');
    if(phase==='disabled'&&!key.startsWith('rear-'))phase='error';
    const words={ready:'正常',disabled:'失能',error:status&&status.fresh===false?'无反馈':status&&status.phase==='disabled'?'失能':'故障',unknown:'未核验'};
    node.dataset.tone={ready:'green',disabled:'amber',error:'red',unknown:'gray'}[phase]||'gray';
    node.textContent=labels[key]+' · '+(words[phase]||phase);
    node.title=String((status&&status.detail)||'暂无新鲜 CAN 反馈');
    const previous=deviceHealthSeen.get(key);
    if(previous!==phase){
      deviceHealthSeen.set(key,phase);
      if(phase==='error'||(previous&&previous!==phase&&phase==='ready')){
        const event=$('#device-health-event');
        if(event){event.textContent=new Date().toLocaleTimeString()+' · '+node.textContent+' · '+node.title;event.dataset.tone=node.dataset.tone;}
        if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(node.textContent+(phase==='error'?' · '+node.title:''),phase==='error'?'error':'success','机械臂');
      }
    }
  }
  const ordered=Object.values(jobs).sort((a,b)=>Number(b.started_at||0)-Number(a.started_at||0));
  for(const name of ['home','recover','pose']){
    const job=jobs[name];if(!job||!['completed','failed','stopped','stale'].includes(job.phase))continue;
    const marker=job.job_id+':'+job.phase;if(deviceCompletionSeen.get(name)===marker)continue;
    deviceCompletionSeen.set(name,marker);
    if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(
      `${name==='home'?'归位':name==='recover'?'恢复':'位姿'} · ${job.phase==='completed'?'已完成':job.phase==='stopped'?'已停止':'未完成'}`,
      job.phase==='failed'||job.phase==='stale'?'error':'success','设备');
  }
  const latest=ordered.find(job=>['running','stopping'].includes(job.phase))||ordered.find(job=>job.log_tail)||ordered[0];
  $('#device-log').textContent=deviceJobLine(latest);
  const summary=$('#device-log-summary');
  if(summary) summary.textContent=latest?`${String(latest.component||'Job').toUpperCase()} · ${latest.phase||'unknown'}`:'进程日志';
  const detail=$('#device-log-details');
  if(detail&&latest&&['failed'].includes(latest.phase))detail.open=true;
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.renderDeviceJobs(payload);
}
async function runDeviceOperation(operation,description,button=null){
  setButtonBusy(button,true,'正在启动');
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(`${description} · 正在请求`,'running','设备');
  try{
    const prepared=await request('/api/console/devices/confirm',{method:'POST',body:JSON.stringify(operation)});
    if(!window.confirm(window.CobotPreferences.text(description+'\n\n该操作将运行固定脚本，确认现场安全后继续。'))){setButtonBusy(button,false);if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(`${description} · 已取消`,'success','设备');return;}
    const job=await request('/api/console/devices/action',{method:'POST',body:JSON.stringify({operation,confirmation_token:prepared.confirmation_token})});
    window.CobotOutputPanel?.follow(job);
    if(operation.component==='rlt'&&operation.action==='start'){
      consoleStatus=await request('/api/console/mode',{method:'POST',body:JSON.stringify({mode:'rlt'})});
      rltSession=null;
    }
    $('#device-message').textContent='已启动 '+job.job_id+'；进度和日志正在实时刷新。';
    $('#device-message').classList.remove('error');
    if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(`${description} · 已启动`,'running','设备');
    await devicePoller.tick();
  }catch(error){
    $('#device-message').textContent='操作未执行：'+error.message;
    $('#device-message').classList.add('error');
    if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.report(`${description} · ${error.message}`,'error','设备');
  }finally{setButtonBusy(button,false);}
}
function bindDeviceControls(){
  $('#device-can').addEventListener('click',event=>runDeviceOperation({component:'can',action:'configure',target:'task2'},'配置 Task2 五臂 CAN（底盘 CAN 保持独立）',event.currentTarget));
  $('#device-arms').addEventListener('click',event=>runDeviceOperation({component:'arms',action:'start'},'启动机械臂 ROS 节点',event.currentTarget));
  $('#device-cameras').addEventListener('click',event=>runDeviceOperation({component:'cameras',action:'start'},'启动三相机 ROS 节点',event.currentTarget));
  $('#device-home').addEventListener('click',event=>runDeviceOperation({component:'home',action:'run',target:$('#device-home-target').value,pose:$('#device-home-pose').value},'机械臂将移动到所选 Home 位姿',event.currentTarget));
  $('#device-recover').addEventListener('click',()=>window.CobotDeviceUI.runRecover($('#device-recover-target').value));
  for(const target of ['reference','warmup','frozen','online'])$('#device-rlt-'+target).addEventListener('click',event=>{
    const model=selectedModel();
    return runDeviceOperation({component:'rlt',action:'start',target,model:model&&model.id},'启动 RLT '+target+' 模式',event.currentTarget);
  });
  $('#device-rlt-stop').addEventListener('click',event=>{const model=selectedModel();return runDeviceOperation({component:'rlt',action:'stop',model:model&&model.id},'停止当前 RLT Session',event.currentTarget);});
  $('#device-rlt-down').addEventListener('click',event=>{const model=selectedModel();return runDeviceOperation({component:'rlt',action:'down',model:model&&model.id},'停止 RLT 并释放模型',event.currentTarget);});
}
function initializePersistentCameraPanel() {
  const panels = $$('.page > .camera-panel');
  if (!panels.length) return;
  persistentCameraPanel = panels[0];
  for (const panel of panels) {
    const page = panel.closest('[data-page]');
    if (!page) continue;
    const mount = document.createElement('div');
    mount.className = 'camera-panel-mount';
    panel.before(mount);
    cameraPanelMounts.set(page.dataset.page, mount);
    if (panel !== persistentCameraPanel) panel.remove();
  }
  mountPersistentCameraPanel(activePageName());
}
function mountPersistentCameraPanel(name) {
  const dock=document.querySelector('#camera-dock');
  if(dock&&persistentCameraPanel){if(persistentCameraPanel.parentElement!==dock)dock.appendChild(persistentCameraPanel);return;}
  const mount = cameraPanelMounts.get(name);
  if (mount && persistentCameraPanel && persistentCameraPanel.parentElement !== mount) {
    mount.appendChild(persistentCameraPanel);
  }
}
function selectPage(name) {
  if(name==='learning')name='operation';
  for (const button of $$(".nav-item")) button.classList.toggle("active", button.dataset.view === name || (name === "learning" && button.dataset.view === "operation"));
  for (const page of $$("[data-page]")) page.classList.toggle("active", page.dataset.page === name);
  for (const button of $$(".collection-choice")) button.classList.toggle("selected", button.dataset.collection === (name === "learning" ? "rlt" : "normal"));
  mountPersistentCameraPanel(name);
  if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.onPage(name);
  if (name === 'system' || name === 'deployment') devicePoller.tick();
  if (name === 'learning' || name === 'training') diagnosticsPoller.tick();
  if (['operation','system','learning','deployment'].includes(name)) { syncCameraStreams(); refreshCameraTriplet(); }
  window.CobotUnifiedCollection?.render();
}
async function chooseCollection(kind) {
  pendingCollectionMode=kind;
  try { window.localStorage.setItem('cobot-collection-last-v1',kind); } catch (_error) {}
  selectPage(kind==='rlt'?'learning':'operation');
  document.body.classList.add('collection-switching');
  renderConsole();
  if(collectionModeTask)return collectionModeTask;
  collectionModeTask=(async()=>{
    while(pendingCollectionMode){
      const next=pendingCollectionMode;
      const ok=consoleStatus?.selected_mode===next || await selectConsoleMode(next);
      if(!ok){if(pendingCollectionMode===next){pendingCollectionMode=null;selectPage(consoleStatus?.selected_mode==='rlt'?'learning':'operation');renderConsole();}continue;}
      if(pendingCollectionMode!==next)continue;
      pendingCollectionMode=null;
      mountEpisodeBrowser(next==='rlt');
      refreshConsole().catch(error=>consoleMessage("模式状态刷新失败："+error.message,true));
    }
  })().finally(()=>{collectionModeTask=null;document.body.classList.remove('collection-switching');if(pendingCollectionMode)chooseCollection(pendingCollectionMode);});
  return collectionModeTask;
}
function syncCameraStreams() {
  const activePage=document.querySelector('[data-page].active');
  for(const image of $$('img[data-camera]')){
    const dock=document.querySelector('#camera-dock');
    const shouldStream=!document.hidden&&((dock&&dock.contains(image))?Boolean(window.CobotWorkspaceUI&&window.CobotWorkspaceUI.cameraVisible()):(activePage&&activePage.contains(image)));
    if(!image.dataset.streamRetryBound){
      image.addEventListener('error',()=>{
        image.removeAttribute('src');
        delete image.dataset.streaming;
        image.dataset.blankSince=String(Date.now());
        const failures=Math.min(6,Number(image.dataset.streamFailures||0)+1);
        const delay=Math.min(10000,500*Math.pow(2,failures-1));
        image.dataset.streamFailures=String(failures);
        image.dataset.retryAt=String(Date.now()+delay);
        setTimeout(syncCameraStreams,delay);
      });
      image.addEventListener('load',()=>{
        delete image.dataset.streamFailures;
        delete image.dataset.retryAt;
      });
      image.dataset.streamRetryBound='true';
    }
    if(shouldStream){
      const expected=`/api/console/cameras/${image.dataset.camera}.mjpg?v=${Date.now()}`;
      if(!image.dataset.streaming&&Date.now()>=Number(image.dataset.retryAt||0)){
        image.src=expected;image.dataset.streaming='true';image.dataset.blankSince=String(Date.now());
      }
    }else if(image.dataset.streaming){
      image.removeAttribute('src');delete image.dataset.streaming;delete image.dataset.blankSince;
    }
  }
}
function repairBlankCameraStreams(status) {
  if(status!=='ready') return;
  const activePage=document.querySelector('[data-page].active');
  const now=Date.now();
  for(const image of $$('img[data-camera]')){
    const dock=document.querySelector('#camera-dock');
    if(dock&&dock.contains(image)?!(window.CobotWorkspaceUI&&window.CobotWorkspaceUI.cameraVisible()):(!activePage||!activePage.contains(image))) continue;
    if(image.naturalWidth>0){delete image.dataset.blankSince;continue;}
    const since=Number(image.dataset.blankSince||now);
    if(!image.dataset.blankSince) image.dataset.blankSince=String(now);
    if(now-since<1500) continue;
    image.removeAttribute('src');delete image.dataset.streaming;
    image.dataset.blankSince=String(now);
  }
  syncCameraStreams();
}
function loadCameraGeneration(generation) {
  const images = $$('img[data-camera]');
  const cameras = [...new Set(images.map(image => image.dataset.camera))];
  const pending = cameras.map(camera => new Promise((resolve, reject) => {
    const preload = new Image();
    preload.onload = () => resolve([camera, preload.src]);
    preload.onerror = reject;
    preload.src = `/api/console/cameras/${camera}.jpg?generation=${generation}&v=${Date.now()}`;
  }));
  return Promise.all(pending).then(rows => {
    if (!cameraViewState || cameraViewState.generation !== generation) return;
    rows.forEach(([camera, source]) => {
      images.filter(node => node.dataset.camera === camera).forEach(image => { image.src = source; });
    });
  });
}
function activePageName() {
  const node = document.querySelector('[data-page].active');
  return node ? node.dataset.page : 'operation';
}
function pagePollingEnabled(name) {
  if (document.hidden) return false;
  return name == null || activePageName() === name;
}
async function refreshCameraTriplet() {
  if (cameraStatusRequestPending) return;
  const dock=document.querySelector('#camera-dock');
  const previewEnabled=dock?Boolean(window.CobotWorkspaceUI&&window.CobotWorkspaceUI.cameraVisible()):['operation','system','learning','deployment'].includes(activePageName());
  if (!pagePollingEnabled() || !previewEnabled) {
    clearTimeout(cameraTimer);
    cameraTimer = setTimeout(refreshCameraTriplet, 500);
    return;
  }
  let status = 'unavailable';
  cameraStatusRequestPending = true;
  try {
    const state = await request('/api/console/cameras');
    const next = window.CobotCameraSyncUI.transition(cameraViewState, state);
    cameraViewState = next; status = next.status;
    const warning = window.CobotCameraSyncUI.warning(state);
    const dockHealth=$('#camera-dock-health');
    if(dockHealth){dockHealth.dataset.status=status;dockHealth.textContent=warning;}
    for (const label of $$('[data-camera-sync-status], #camera-sync-status')) {
      label.dataset.status = status;
      if (!textSelectionActive()) label.textContent = warning;
    }
    for (const node of $$('[data-camera-health]')) {
      const age = state.age_sec && state.age_sec[node.dataset.cameraHealth];
      const resolution=state.resolution&&state.resolution[node.dataset.cameraHealth];
      const size=Array.isArray(resolution)?resolution.join('×'):'—';
      const fps=Number(state.preview_fps||0).toFixed(1);
      node.textContent = `${fps} FPS · ${size} · Q${state.jpeg_quality||'—'} · ${age==null?'—':Math.round(Number(age)*1000)+' ms'}`;
    }
    repairBlankCameraStreams(status);
    cameraFailures = 0;
  } catch (error) {
    cameraFailures += 1;
    const dockHealth=$('#camera-dock-health');
    if(dockHealth){dockHealth.dataset.status='unavailable';dockHealth.textContent='相机服务不可用 · 正在重试';dockHealth.title=error.message;}
    for (const label of $$('[data-camera-sync-status], #camera-sync-status')) {
      label.dataset.status = 'unavailable';
      if (!textSelectionActive()) label.textContent = '相机连接失败，正在重试';
    }
  } finally {
    cameraStatusRequestPending = false;
    clearTimeout(cameraTimer);
    cameraTimer = setTimeout(refreshCameraTriplet, window.CobotCameraSyncUI.retryDelay(status, cameraFailures));
  }
}
for (const image of []) { void image; } // legacy unit-test bootstrap boundary.
for (const button of $$(".nav-item")) {
  button.addEventListener("click", () => {
    if(button.dataset.view==='operation'){
      let last='normal';try{last=window.localStorage.getItem('cobot-collection-last-v1')||'normal';}catch(_error){}
      chooseCollection(last);
    }else selectPage(button.dataset.view);
  });
}
for(const button of $$('.collection-choice'))button.addEventListener('click',()=>chooseCollection(button.dataset.collection));
const brand=$('.brand');
if(brand)brand.addEventListener('click',event=>{event.preventDefault();brand.classList.add('brand-touched');setTimeout(()=>brand.classList.remove('brand-touched'),480);});
diagnosticsPoller = window.CobotDiagnosticsUI.createPoller(
  () => request("/api/console/diagnostics"), renderDiagnostics, diagnosticsFailed,
);
devicePoller = window.CobotDiagnosticsUI.createPoller(()=>request("/api/console/devices"),renderDevices,error=>{$("#device-message").textContent="设备状态不可用："+error.message;window.CobotDeviceUI?.updateLifecycleControls({});});
bindDeviceControls();
$("#rlt-data-root").addEventListener("input", () => { rltStorageDirty = true; });
$("#rlt-save-storage").addEventListener("click", saveRltStorage);
$("#mode-normal").addEventListener("click", () => selectConsoleMode("normal"));
$("#mode-rlt").addEventListener("click", () => selectConsoleMode("rlt"));
$("#rlt-release-select").addEventListener("change", renderReleasePicker);
$("#rlt-release-switch").addEventListener("click", switchRelease);
$("#rlt-model-select").addEventListener("change", renderModelPicker);
$("#rlt-model-switch").addEventListener("click", switchModel);
for (const name of ["arm", "start", "pause", "resume", "success", "failure", "abort", "next", "stop", "prepare", "marker", "save"]) {
  $("#rlt-" + name).addEventListener("click", event => withButtonBusy(event.currentTarget, () => operateRlt(name)));
}
$("#start").addEventListener("click", event => withButtonBusy(event.currentTarget, start));
$("#prepare-storage").addEventListener("click", () => prepareStorage(false));
$("#browse-directories").addEventListener("click", () => {
  if (normalPathPicker) normalPathPicker.refresh();
});
for (const name of ["pause", "resume", "marker", "stop", "discard"]) {
  $("#" + name).addEventListener("click", event => withButtonBusy(event.currentTarget, () => operate(name)));
}
$("#stop-home").addEventListener("click", event => withButtonBusy(event.currentTarget, finalizeAndHome, "保存并复位"));
$("#capture-home-enabled").addEventListener("change", () => setTimeout(updateButtons,0));
$("#capture-home-now").addEventListener("click",event=>withButtonBusy(event.currentTarget,()=>homeCollection(historyIsRlt()?'rlt':'normal')));
$("#rlt-home-now").addEventListener("click",event=>withButtonBusy(event.currentTarget,()=>homeCollection('rlt')));
window.addEventListener("keydown", event => handleEpisodeHistoryShortcut(event).catch(error => historyMessage(error.message, true)));
window.addEventListener("keydown", handleCaptureShortcut);
window.addEventListener("keydown", handleRltShortcut);
$("#refresh-history").addEventListener("click", event => withButtonBusy(event.currentTarget, () => refreshHistory({loadSelected:true})).catch((error) => message(error.message, true)));
$("#load-history").addEventListener("click", event => withButtonBusy(event.currentTarget, loadHistory).catch((error) => message(error.message, true)));
$("#episode-history").addEventListener("change", () => loadHistory().catch(error => message(error.message, true)));
$("#episode-history").addEventListener("wheel", event => {
  if (!event.deltaY) return;
  event.preventDefault();
  moveHistorySelection(event.deltaY > 0 ? 1 : -1).catch(error => historyMessage(error.message, true));
}, {passive:false});
$("#show-live").addEventListener("click", showLive);
$("#load-replay").addEventListener("click", event => withButtonBusy(event.currentTarget, () => loadHistory(false).then(loadReplay)).catch(error => message(error.message, true)));
$("#delete-history").addEventListener("click", event => withButtonBusy(event.currentTarget, deleteHistory));
$("#save-review").addEventListener("click", event => withButtonBusy(event.currentTarget, saveReview));
for (const input of $$("#capture-form input")) {
  input.addEventListener("input", () => {
    showLive();
    storagePrepared = false;
    $("#episode-directory").textContent = "配置已更改，请重新检查";
    $("#next-episode").textContent = "—";
    updateButtons();
  });
}
replayTimeline = window.CobotReplayTimeline.create(
  $("#episode-video"),
  { slider: $("#replay-slider"), markers: $("#replay-markers"), label: $("#replay-time") },
  { onNode: node => {
    if (node) $("#replay-status").textContent = `${historyNodeLabel(node)}；视频已吸附到该节点。`;
  } },
);

async function initializeCaptureProfile() {
  try {
    const config = await request("/api/console/config");
    if(config.read_only && !document.getElementById("readonly-preview-banner")){
      const banner=document.createElement("div");banner.id="readonly-preview-banner";
      banner.textContent="只读预览 · 不控制机器人 / Read-only preview · Robot actions disabled";
      banner.style.cssText="position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:10000;padding:5px 16px;border:1px solid #767676;border-radius:0 0 8px 8px;background:#272727;color:#dedede;font-size:12px;pointer-events:none;white-space:nowrap";
      document.body.append(banner);
    }
    STORAGE_KEY = `cobot-data-console-config:${config.profile}`;
    window.CobotRltHome.initialize(config.profile);
    window.CobotCaptureHome.initialize(config.profile);
    refreshCaptureHomePoses();
    const form = $("#capture-form");
    const recentDirectories = previousRecordingDirectories();
    const recentRoot = recentDirectories.find(path => typeof path === "string" && path.trim()) || config.normal_data_root;
    form.elements.namedItem("data_root").value = recentRoot;
    renderRecordingDirectories([recentRoot, ...(config.data_root_choices || []), ...recentDirectories]);
    const fetchDirectories = value => request("/api/segmented-teach/storage/directories?path=" + encodeURIComponent(value));
    const pathStorage = window.localStorage;
    normalPathPicker = window.CobotPathPicker.create({
      input: form.elements.namedItem("data_root"),
      panel: $("#directory-browser"), list: $("#directory-options"), hint: $("#directory-hint"),
      fetchDirectories, storage: pathStorage, storageKey: "cobot-recent-paths-v2:" + config.profile,
    });
    rltPathPicker = window.CobotPathPicker.create({
      input: $("#rlt-data-root"),
      panel: $("#rlt-directory-browser"), list: $("#rlt-directory-options"), hint: $("#rlt-directory-hint"),
      fetchDirectories, storage: pathStorage, storageKey: "cobot-recent-rlt-paths-v2:" + config.profile,
      onChange: () => { rltStorageDirty = true; },
    });
    await refreshModels();
    refresh();
    refreshConsole();
    prepareStorage(true);
  } catch (error) {
    message("无法读取采集目录配置：" + error.message, true);
  }
}
initializeCaptureProfile();
initializePersistentCameraPanel();
if(window.CobotWorkspaceUI)window.CobotWorkspaceUI.cameraChanged=()=>{syncCameraStreams();refreshCameraTriplet();};
syncCameraStreams();
refreshCameraTriplet();
const initialPage = new URLSearchParams(window.location.search).get('view');
if (['system','operation','training','deployment','outputs','host'].includes(initialPage)) selectPage(initialPage);
document.addEventListener("selectionchange", scheduleAfterSelection);
document.addEventListener("visibilitychange",()=>{syncCameraStreams();if(!document.hidden)refreshCameraTriplet();});
document.addEventListener("cobot:language", () => {
  refresh();
  refreshConsole();
  if (["learning", "training"].includes(activePageName())) diagnosticsPoller.tick();
  if (activePageName() === "system") devicePoller.tick();
});
window.setInterval(() => { if (pagePollingEnabled('operation') && !historyIsRlt() && !textSelectionActive()) refresh(); }, 750);
window.setInterval(() => { if (!document.hidden && !textSelectionActive()) refreshConsole(); }, 1000);
window.setInterval(() => { if (pagePollingEnabled('operation') && historyIsRlt() && !textSelectionActive()) refreshRltRecorderBrowser(); }, 750);
window.setInterval(() => { if (!document.hidden && !textSelectionActive() && (activePageName() === 'operation' && historyIsRlt())) refreshReleases(); }, 10000);
refreshReleases();
window.setInterval(() => { if (!document.hidden && !textSelectionActive() && (activePageName() === 'operation' && historyIsRlt())) refreshModels(); }, 5000);
refreshModels();
window.setInterval(() => {
  if (!document.hidden && !textSelectionActive() && ['learning','training'].includes(activePageName())) diagnosticsPoller.tick();
}, 1000);
window.setInterval(() => { if (pagePollingEnabled('system') && !textSelectionActive()) devicePoller.tick(); }, 1000);
window.setInterval(() => { if (pagePollingEnabled('deployment') && !textSelectionActive()) devicePoller.tick(); }, 2000);
window.setInterval(() => { if (pagePollingEnabled('operation') && !textSelectionActive()) devicePoller.tick(); }, 3000);
