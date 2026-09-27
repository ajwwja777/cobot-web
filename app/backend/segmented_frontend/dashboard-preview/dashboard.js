(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const partOrder = ['rear-left', 'rear-right', 'mid', 'front-left', 'front-right', 'camera_left', 'camera_high', 'camera_right', 'gripper_left', 'gripper_right'];
  const parts = {
    'rear-left': {name: '左后臂', category: '机械臂', icon: 'L', source: '被动 CAN 反馈', action: '操作台 · 恢复/归位'},
    'rear-right': {name: '右后臂', category: '机械臂', icon: 'R', source: '被动 CAN 反馈', action: '操作台 · 恢复/归位'},
    mid: {name: '中臂', category: '机械臂 / 相机', icon: 'M', source: '被动 CAN 反馈', action: '操作台 · 中臂归位'},
    'front-left': {name: '左前臂', category: '机械臂', icon: 'L', source: '被动 CAN 反馈', action: '操作台 · 恢复/归位'},
    'front-right': {name: '右前臂', category: '机械臂', icon: 'R', source: '被动 CAN 反馈', action: '操作台 · 恢复/归位'},
    camera_left: {name: '左腕相机', category: '视觉', icon: '◉', source: '同步相机预览', action: '操作台 · 相机节点'},
    camera_high: {name: '中臂相机', category: '视觉', icon: '◉', source: '同步相机预览', action: '操作台 · 相机节点'},
    camera_right: {name: '右腕相机', category: '视觉', icon: '◉', source: '同步相机预览', action: '操作台 · 相机节点'},
    gripper_left: {name: '左前夹爪', category: '夹爪', icon: '⌁', source: '左前臂 CAN 反馈', action: '操作台 · 夹爪检查'},
    gripper_right: {name: '右前夹爪', category: '夹爪', icon: '⌁', source: '右前臂 CAN 反馈', action: '操作台 · 夹爪检查'},
  };
  const cameras = ['camera_left', 'camera_high', 'camera_right'];
  const state = {selected: 'front-right', devices: null, console: null, camera: null, connected: false, lastResponse: 0, lastDevices: 0, lastCamera: 0, cameraGeneration: -1, imageGeneration: -1, imagePending: false, deviceBusy: false, consoleBusy: false, cameraBusy: false, events: [], signature: new Map()};

  function clockText(time = Date.now()) { return new Date(time).toLocaleTimeString('zh-CN', {hour12: false}); }
  function ageText(time) { if (!time) return '—'; const seconds = Math.max(0, Math.floor((Date.now() - time) / 1000)); return seconds < 2 ? '刚刚' : `${seconds} 秒前`; }
  function phaseText(phase) { return ({ready: '正常', error: '异常', offline: '未启动', disabled: '失能', running: '运行中', stale: '已过期', fault: '故障', loading: '加载中'})[phase] || '未确认'; }
  function normalizedPhase(value) { const phase = String(value || 'unknown'); return ['ready','error','offline','disabled','running','stale','fault'].includes(phase) ? phase : 'unknown'; }
  function safeDetail(value, fallback) { return typeof value === 'string' && value.trim() ? value.trim() : fallback; }
  function appendEvent(text, tone = 'ready') {
    const li = document.createElement('li');
    const bullet = document.createElement('span'); bullet.className = 'event-bullet'; bullet.dataset.state = tone;
    const content = document.createElement('span'); content.textContent = text;
    const time = document.createElement('time'); time.dateTime = new Date().toISOString(); time.textContent = clockText();
    li.append(bullet, content, time);
    const list = $('#event-list');
    if (list.querySelector('.event-empty')) list.replaceChildren();
    list.prepend(li);
    while (list.children.length > 12) list.lastElementChild.remove();
  }
  function changed(key, signature, label, phase) {
    const before = state.signature.get(key);
    if (before === signature) return;
    state.signature.set(key, signature);
    if (before !== undefined) appendEvent(`${label}：${phaseText(phase)}`, phase === 'error' || phase === 'fault' ? 'error' : phase === 'offline' || phase === 'disabled' ? 'warning' : 'ready');
  }
  function networkReady() {
    state.connected = true; state.lastResponse = Date.now();
    $('#connection-dot').dataset.state = 'ready';
    $('#connection-text').textContent = '控制台已连接';
  }
  function networkFailure() {
    if (Date.now() - state.lastResponse < 7000) return;
    state.connected = false;
    $('#connection-dot').dataset.state = 'error';
    $('#connection-text').textContent = '控制台连接中断';
    renderSafety();
  }
  async function readJson(url, timeout = 6000) {
    const response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(timeout)});
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    const payload = await response.json();
    networkReady();
    return payload;
  }

  function partState(id) {
    if (id.startsWith('camera_')) {
      const camera = state.camera;
      const age = camera?.age_sec?.[id];
      const stale = camera?.stale_keys?.includes(id) || typeof age !== 'number' || age > 1.2 || Date.now() - state.lastCamera > 2500;
      return {phase: !camera ? 'unknown' : stale ? 'error' : camera.status === 'ready' ? 'ready' : 'error', detail: !camera ? '等待相机状态' : stale ? '画面已停止更新或反馈过期' : `画面更新中 · ${Number(camera.preview_fps || 0).toFixed(1)} FPS`, updated: state.lastCamera};
    }
    if (state.devices && Date.now() - state.lastDevices > 3500) return {phase: 'stale', detail: '机械臂状态接口未及时更新', updated: state.lastDevices};
    const feedback = state.devices?.systems?.arms_feedback;
    if (!feedback) return {phase: 'unknown', detail: '等待机械臂 CAN 反馈', updated: state.lastDevices};
    if (id.startsWith('gripper_')) {
      const arm = feedback[id === 'gripper_left' ? 'front-left' : 'front-right'];
      if (!arm?.fresh) return {phase: 'error', detail: '对应前臂 CAN 反馈缺失，夹爪状态无法确认', updated: state.lastDevices};
      return {phase: normalizedPhase(arm.gripper?.phase), detail: safeDetail(arm.gripper?.detail, '夹爪状态未确认'), updated: state.lastDevices};
    }
    const arm = feedback[id];
      return {phase: normalizedPhase(arm?.phase), detail: arm?.detail === 'CAN feedback missing' ? 'CAN 反馈缺失：没有收到该机械臂的新鲜状态帧' : safeDetail(arm?.detail, '无最新反馈'), updated: state.lastDevices};
  }
  function renderPart(id) {
    const part = parts[id], value = partState(id), position = partOrder.indexOf(id) + 1;
    $$('.machine-part').forEach((element) => { element.classList.toggle('selected', element.dataset.part === id); element.dataset.state = partState(element.dataset.part).phase; });
    $$('.camera-tile').forEach((element) => element.classList.toggle('is-focused', element.dataset.camera === id));
    $('#focus-index').textContent = `${String(position).padStart(2, '0')} / ${partOrder.length}`;
    $('#focus-icon').textContent = part.icon;
    $('#focus-category').textContent = part.category;
    $('#focus-name').textContent = part.name;
    $('#focus-status').dataset.state = value.phase;
    $('#focus-status').lastChild.textContent = phaseText(value.phase);
    $('#focus-detail').textContent = value.detail;
    $('#focus-source').textContent = part.source;
    $('#focus-updated').textContent = ageText(value.updated);
    $('#focus-action-label').textContent = part.action;
    $('#focus-link').href = '/?view=system';
  }
  function renderSystems() {
    const systems = state.devices?.systems || {};
    const labels = {can: 'CAN', roscore: 'ROS Core', arms: '机械臂节点', cameras: '相机节点', rlt: 'RLT 后端'};
    const known = {'5/5 CAN up': '5/5 接口已启动', 'ROS Master reachable': '主节点可连接', '3/3 arm coordinators': '3/3 节点运行', '3/3 camera nodes': '3/3 节点运行', 'RLT backend absent': '后端未启动'};
    for (const [key, label] of Object.entries(labels)) {
      const item = $(`.metric-row[data-system="${key}"]`), value = systems[key], phase = state.devices && Date.now() - state.lastDevices > 3500 ? 'stale' : normalizedPhase(value?.phase);
      item.dataset.state = phase;
      item.querySelector('strong').textContent = phase === 'stale' ? '反馈已过期' : known[value?.detail] || value?.detail || phaseText(phase);
      item.title = value?.detail || '';
      changed(`system:${key}`, phase + ':' + String(value?.detail || ''), label, phase);
    }
    $('#devices-update').textContent = '更新于 ' + ageText(state.lastDevices);
    renderPart(state.selected);
    renderSafety();
  }
  function renderSafety() {
    const banner = $('#safety-banner'), title = $('#safety-title'), detail = $('#safety-detail');
    if (!state.connected || Date.now() - state.lastResponse > 8500 || !state.devices || Date.now() - state.lastDevices > 3500) {
      banner.dataset.state = 'unknown'; title.textContent = '状态链路尚未确认'; detail.textContent = '连接恢复并取得最新机械臂反馈之前不要执行动作。';
    } else {
      const feedback = state.devices.systems?.arms_feedback || {};
      const faulted = ['front-left','front-right','mid','rear-left','rear-right'].filter((id) => feedback[id]?.phase !== 'ready' || !feedback[id]?.fresh);
      const nodesReady = ['can','roscore','arms'].every((id) => state.devices.systems?.[id]?.phase === 'ready');
      if (faulted.length || !nodesReady) {
        banner.dataset.state = 'error'; title.textContent = '设备反馈异常 · 暂勿执行机械臂动作';
        detail.textContent = faulted.length ? `${faulted.length} 个机械臂状态未就绪：${faulted.map((id) => parts[id].name).join('、')}` : 'CAN、ROS Core 或机械臂节点未就绪';
      } else {
        banner.dataset.state = 'ready'; title.textContent = '设备反馈正常'; detail.textContent = '操作前仍须由现场人员检查急停、工作区和位姿。';
      }
    }
    $('#safety-age').textContent = '反馈 ' + ageText(state.lastDevices);
  }
  function renderConsole() {
    const d = state.console;
    if (!d) return;
    const counts = d.recording_counts || {};
    for (const key of ['demonstrations','warmup','online']) $(`#count-${key}`).textContent = String(counts[key]?.total ?? '—');
    $('#data-profile').textContent = d.data_profile || '—';
    const model = d.rlt_model || {};
    $('#model-cohort').textContent = model.cohort || '未选择模型';
    $('#model-name').textContent = model.label || '当前模型不可用';
    $('#model-description').textContent = model.description || '模型身份和可用性以服务器记录为准。';
    $('#model-kind').textContent = model.kind || '—';
    $('#model-runtime').textContent = d.rlt_backend_phase || 'offline';
    $('#model-checkpoint').textContent = model.checkpoint_step == null ? '—' : `step ${model.checkpoint_step}`;
    changed('console:backend', String(d.rlt_backend_phase), 'RLT 后端', d.rlt_backend_phase === 'fault' ? 'error' : d.rlt_backend_phase === 'offline' ? 'offline' : 'ready');
    changed('console:model', model.id || '', '模型选择', model.available ? 'ready' : 'disabled');
  }
  function renderCamera() {
    const c = state.camera;
    const cameraOK = Boolean(c && c.status === 'ready' && !c.stale_keys?.length && Date.now() - state.lastCamera < 2500);
    $('#camera-health').dataset.state = cameraOK ? 'ready' : c ? 'error' : 'unknown';
    $('#camera-health').lastChild.textContent = cameraOK ? `${Number(c.preview_fps || 0).toFixed(1)} FPS` : c ? '画面异常' : '连接中';
    $('#camera-skew').textContent = c?.skew_ms == null ? 'skew —' : `skew ${Math.round(c.skew_ms)} ms`;
    for (const key of cameras) {
      const stateLabel = $(`#camera-state-${key}`), ageLabel = $(`#camera-age-${key}`), value = partState(key);
      stateLabel.dataset.state = value.phase;
      stateLabel.textContent = value.phase === 'ready' ? 'LIVE' : '画面未更新';
      ageLabel.textContent = typeof c?.age_sec?.[key] === 'number' ? `${Math.round(c.age_sec[key] * 1000)} ms ago` : '—';
    }
    changed('camera:group', c ? `${c.status}:${c.stale_keys?.join(',')}` : 'unknown', '三相机画面', cameraOK ? 'ready' : 'error');
    renderPart(state.selected);
  }
  function preloadImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image(); let done = false;
      const timer = setTimeout(() => { if (!done) { done = true; reject(new Error('image_timeout')); } }, 1800);
      img.onload = () => { if (!done) { done = true; clearTimeout(timer); resolve(url); } };
      img.onerror = () => { if (!done) { done = true; clearTimeout(timer); reject(new Error('image_failed')); } };
      img.src = url;
    });
  }
  async function updateCameraImages(generation) {
    if (state.imagePending || generation === state.imageGeneration || document.hidden) return;
    state.imagePending = true;
    const urls = cameras.map((key) => `/api/console/cameras/${key}.jpg?generation=${encodeURIComponent(generation)}`);
    try {
      await Promise.all(urls.map(preloadImage));
      if (generation <= state.imageGeneration) return;
      cameras.forEach((key, i) => { $(`#image-${key}`).src = urls[i]; });
      state.imageGeneration = generation;
    } catch (_) {
      // The next generation is retried without replacing any of the three visible frames.
    } finally { state.imagePending = false; }
  }
  async function pollDevices() {
    if (state.deviceBusy || document.hidden) return;
    state.deviceBusy = true;
    try { state.devices = await readJson('/api/console/devices', 6500); state.lastDevices = Date.now(); renderSystems(); if (!state.eventsInitialized) { appendEvent('设备与节点状态已接入'); state.eventsInitialized = true; } }
    catch (_) { networkFailure(); renderSystems(); }
    finally { state.deviceBusy = false; }
  }
  async function pollConsole() {
    if (state.consoleBusy || document.hidden) return;
    state.consoleBusy = true;
    try { state.console = await readJson('/api/console/status', 6000); renderConsole(); }
    catch (_) { networkFailure(); }
    finally { state.consoleBusy = false; }
  }
  async function pollCamera() {
    if (state.cameraBusy || document.hidden) return;
    state.cameraBusy = true;
    try {
      state.camera = await readJson('/api/console/cameras', 2500);
      state.lastCamera = Date.now(); state.cameraGeneration = Number(state.camera.generation || 0);
      renderCamera();
      if (state.camera.status === 'ready' && state.cameraGeneration > 0) updateCameraImages(state.cameraGeneration);
    } catch (_) { networkFailure(); renderCamera(); }
    finally { state.cameraBusy = false; }
  }
  function initialize() {
    $$('.machine-part').forEach((element) => {
      element.addEventListener('click', () => { state.selected = element.dataset.part; renderPart(state.selected); });
      element.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); element.dispatchEvent(new MouseEvent('click', {bubbles: true})); } });
    });
    $('#refresh-now').addEventListener('click', () => { pollDevices(); pollConsole(); pollCamera(); });
    $('#clock').textContent = clockText(); setInterval(() => { $('#clock').textContent = clockText(); $('#focus-updated').textContent = ageText(partState(state.selected).updated); $('#devices-update').textContent = '更新于 ' + ageText(state.lastDevices); renderSystems(); }, 1000);
    pollDevices(); pollConsole(); pollCamera();
    setInterval(pollDevices, 1000);
    setInterval(pollConsole, 2000);
    setInterval(pollCamera, 120);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { pollDevices(); pollConsole(); pollCamera(); } });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, {once: true}); else initialize();
})();
