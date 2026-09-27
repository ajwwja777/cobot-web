"use strict";
(function spatialConsole(root) {
  const $ = selector => document.querySelector(selector);
  const armIds = ["front-left", "front-right", "mid", "rear-left", "rear-right"];
  const gripperIds = ["gripper-left", "gripper-right"];
  const cameraIds = ["camera_left", "camera_high", "camera_right"];
  const names = {
    "front-left": ["左前臂", "Front left arm"], "front-right": ["右前臂", "Front right arm"],
    mid: ["中臂", "Center arm"], "rear-left": ["左后臂", "Rear left arm"],
    "rear-right": ["右后臂", "Rear right arm"], "gripper-left": ["左夹爪", "Left gripper"],
    "gripper-right": ["右夹爪", "Right gripper"], camera_left: ["左腕相机", "Left wrist camera"],
    camera_high: ["中臂相机", "Center camera"], camera_right: ["右腕相机", "Right wrist camera"],
    computer: ["工控机", "Control computer"]
  };
  const groups = {
    front: ["front-left", "front-right"], rear: ["rear-left", "rear-right"],
    all: armIds, mid: ["mid"], gripper: gripperIds
  };
  const phases = {
    ready: ["正常", "Ready"], error: ["异常", "Error"], disabled: ["失能", "Disabled"],
    stale: ["反馈过期", "Stale"], offline: ["未启动", "Offline"], unknown: ["未核验", "Unknown"]
  };
  const state = {devices:null, cameras:null, host:null, lastDevices:0, lastCameras:0, lastHost:0,
    focus:"front-right", selected:new Set(), busy:false, pollBusy:false, lastAlert:"", actionStarted:false};
  function en() { return root.CobotPreferences?.language === "en"; }
  function t(zh, english) { return en() ? english : zh; }
  function name(id) { return (names[id] || [id,id])[en() ? 1 : 0]; }
  function text(id, zh, english) { const el=$(id); if(el) el.textContent=t(zh,english); }
  function arm(id, x1, y1, x2, y2, x3, y3, labelX, labelY) {
    return `<g class="spatial-node spatial-arm" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <path class="arm-link" d="M${x1} ${y1} L${x2} ${y2} L${x3} ${y3}"/>
      <circle class="arm-base" cx="${x1}" cy="${y1}" r="22"/><circle class="arm-joint" cx="${x2}" cy="${y2}" r="11"/>
      <circle class="arm-tip" cx="${x3}" cy="${y3}" r="12"/><circle class="spatial-light" cx="${x1+16}" cy="${y1-15}" r="5"/>
      <text class="arm-label" x="${labelX}" y="${labelY}" text-anchor="middle" data-name="${id}"></text></g>`;
  }
  function camera(id, x, y) {
    return `<g class="spatial-node spatial-camera" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <rect class="camera-box" x="${x-15}" y="${y-11}" width="30" height="22" rx="6"/><circle class="camera-lens" cx="${x}" cy="${y}" r="6"/>
      <circle class="spatial-light" cx="${x+12}" cy="${y-9}" r="3"/></g>`;
  }
  function gripper(id, x, y) {
    return `<g class="spatial-node spatial-gripper" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <circle class="gripper-halo" cx="${x}" cy="${y}" r="17"/><path class="gripper-fingers" d="M${x-8} ${y-8}v13h5 M${x+8} ${y-8}v13h-5"/>
      <circle class="spatial-light" cx="${x+13}" cy="${y-13}" r="3"/></g>`;
  }
  function sceneMarkup() {
    return `<div class="spatial-floor"><svg class="spatial-scene" viewBox="0 0 940 480" role="img" aria-label="Cobot component status" preserveAspectRatio="xMidYMid meet">
      <defs><linearGradient id="scene-table" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#1a3644"/><stop offset="1" stop-color="#102330"/></linearGradient>
      <filter id="scene-glow"><feGaussianBlur stdDeviation="7" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <rect class="table-outline" x="35" y="34" width="870" height="412" rx="22" fill="url(#scene-table)"/>
      <path class="table-grid" d="M55 158H885 M55 320H885 M317 50V430 M626 50V430"/>
      <path class="spatial-connection" data-link="left" d="M298 284 C350 235 490 176 596 104"/>
      <path class="spatial-connection" data-link="right" d="M421 284 C515 333 680 245 791 104"/>
      <path class="spatial-connection" data-link="center" d="M459 226 C481 283 489 335 487 375"/>
      <rect class="work-object" x="553" y="290" width="76" height="34" rx="8"/><path class="work-object-line" d="M578 299v15 M603 299v15"/>
      ${arm("rear-left",150,373,218,326,298,284,150,427)}
      ${arm("rear-right",324,373,370,324,421,284,324,427)}
      ${arm("mid",453,86,456,152,459,226,453,56)}
      ${arm("front-left",598,100,573,171,557,250,598,57)}
      ${arm("front-right",790,100,750,168,697,252,790,57)}
      ${camera("camera_high",457,180)}${camera("camera_left",569,189)}${camera("camera_right",737,185)}
      ${gripper("gripper-left",557,250)}${gripper("gripper-right",697,252)}
      <g class="spatial-node spatial-computer" data-spatial="computer" role="button" tabindex="0" aria-pressed="false">
        <rect class="computer-box" x="435" y="375" width="105" height="44" rx="9"/><rect class="computer-screen" x="450" y="384" width="23" height="20" rx="3"/>
        <circle class="spatial-light" cx="526" cy="385" r="4"/><text class="computer-label" x="487" y="438" text-anchor="middle" data-name="computer"></text></g>
      </svg></div>`;
  }
  function mount() {
    const panel=$(".infrastructure-panel"), grid=panel?.querySelector(".device-dashboard");
    if(!panel || !grid || $("#spatial-layout")) return;
    const section=document.createElement("section"); section.className="spatial-layout"; section.id="spatial-layout";
    section.innerHTML=`<div class="spatial-card"><header class="spatial-head"><h3 id="spatial-heading"></h3><div class="spatial-groups">
      <button data-group="front" type="button"></button><button data-group="rear" type="button"></button><button data-group="all" type="button"></button><button data-group="mid" type="button"></button><button data-group="gripper" type="button"></button>
    </div></header>${sceneMarkup()}</div><div class="spatial-detail"><div class="spatial-detail-head"><span class="spatial-focus-icon" id="spatial-focus-icon">◈</span><div><h3 id="spatial-focus-name"></h3><span class="spatial-phase" id="spatial-focus-phase"></span></div></div>
      <p class="spatial-detail-value" id="spatial-focus-detail"></p><div class="spatial-selection" id="spatial-selection"></div>
      <div class="spatial-actions" id="spatial-arm-actions"><label><span id="spatial-pose-label"></span><select id="spatial-pose"></select></label>
        <div class="spatial-button-row"><button type="button" id="spatial-home"></button><button type="button" id="spatial-recover" class="secondary"></button></div>
        <label><span id="spatial-capture-label"></span><input id="spatial-capture-name" maxlength="64" autocomplete="off"></label><button type="button" id="spatial-capture" class="secondary"></button><small id="spatial-action-limit"></small></div>
      <div class="spatial-actions" id="spatial-computer-actions" hidden><div class="spatial-computer-facts" id="spatial-computer-facts"></div><div class="spatial-button-row"><button type="button" id="spatial-can"></button><button type="button" id="spatial-can-reset" class="secondary"></button></div><button type="button" id="spatial-host" class="secondary"></button></div>
      <div class="spatial-actions" id="spatial-camera-actions" hidden><button type="button" id="spatial-open-cameras" class="secondary"></button></div>
    </div>`;
    grid.before(section);
    const poseCard=grid.querySelector('[data-card-id="arms"]');
    if(poseCard){ poseCard.querySelector(".card-head h3").textContent=t("位姿管理","Pose management"); poseCard.querySelector(".physical-arm-map")?.classList.add("hidden"); }
    for(const item of section.querySelectorAll("[data-spatial]")){
      item.addEventListener("click",()=>choose(item.dataset.spatial));
      item.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();choose(item.dataset.spatial);}});
    }
    for(const item of section.querySelectorAll("[data-group]"))item.addEventListener("click",()=>selectGroup(item.dataset.group));
    $("#spatial-home").addEventListener("click",home);
    $("#spatial-recover").addEventListener("click",recover);
    $("#spatial-capture").addEventListener("click",capture);
    $("#spatial-capture-name").addEventListener("input",renderActions);
    $("#spatial-can").addEventListener("click",()=>$("#device-can")?.click());
    $("#spatial-can-reset").addEventListener("click",()=>$("#device-can-reset")?.click());
    $("#spatial-host").addEventListener("click",()=>$(".nav-item[data-view=host]")?.click());
    $("#spatial-open-cameras").addEventListener("click",()=>$("#camera-nav")?.click());
    installRail(); localize(); render();
    document.addEventListener("cobot:language",()=>{ localize(); render(); });
    root.setInterval(poll,1000); poll();
  }
  function choose(id) {
    state.focus=id;
    if(id==="computer" || cameraIds.includes(id))state.selected.clear();
    else if(state.selected.has(id))state.selected.delete(id);
    else state.selected.add(id);
    if(id==="computer")refreshHost();
    render();
  }
  function selectGroup(group) {
    state.selected=new Set(groups[group] || []);
    state.focus=groups[group]?.[0] || "front-right";
    render();
  }
  function selectedTarget() {
    const ids=[...state.selected];
    return Object.keys(groups).find(key=>groups[key].length===ids.length && groups[key].every(id=>state.selected.has(id))) || null;
  }
  function status(id) {
    if(id==="computer"){
      const can=state.devices?.systems?.can, ros=state.devices?.systems?.roscore;
      return {phase:!state.devices?'unknown':can?.phase==='ready'&&ros?.phase==='ready'?'ready':'error',
        detail:!state.devices?t("等待状态","Waiting for status"):`CAN ${(phases[can?.phase] || phases.unknown)[en()?1:0]} · ROS ${(phases[ros?.phase] || phases.unknown)[en()?1:0]}`};
    }
    if(cameraIds.includes(id)){
      const camera=state.cameras, age=camera?.age_sec?.[id];
      const stale=!camera || Date.now()-state.lastCameras>2500 || camera.stale_keys?.includes(id) || typeof age!=="number" || age>1.2;
      return {phase:!camera?'unknown':stale?'stale':camera.status==='ready'?'ready':'error',
        detail:stale?t("画面未更新","Video not updating"):`${Number(camera.preview_fps || 0).toFixed(1)} FPS · ${Math.round(age*1000)} ms`};
    }
    if(!state.devices)return {phase:"unknown",detail:t("等待状态","Waiting for status")};
    if(Date.now()-state.lastDevices>3500)return {phase:"stale",detail:t("反馈已过期","Feedback stale")};
    const key=id.startsWith("gripper-")?`front-${id.slice(8)}`:id;
    const arm=state.devices.systems?.arms_feedback?.[key];
    if(!arm?.fresh)return {phase:"error",detail:t("CAN 反馈缺失","CAN feedback missing")};
    const value=id.startsWith("gripper-")?arm.gripper:arm;
    const detail=value?.detail === "CAN feedback missing" ? t("CAN 反馈缺失","CAN feedback missing") : value?.detail;
    return {phase:value?.phase || "unknown",detail:detail || t("状态未核验","Status unverified")};
  }
  function localize() {
    const labels={front:["前双臂","Front pair"],rear:["后双臂","Rear pair"],all:["五臂","All arms"],mid:["中臂","Center arm"],gripper:["双夹爪","Grippers"]};
    $(".spatial-scene")?.setAttribute("aria-label",t("Cobot 部件状态","Cobot component status"));
    text("#spatial-heading","设备","Devices");
    for(const el of document.querySelectorAll("[data-group]"))el.textContent=labels[el.dataset.group]?.[en()?1:0] || el.dataset.group;
    for(const el of document.querySelectorAll("[data-name]"))el.textContent=name(el.dataset.name);
    text("#spatial-pose-label","目标位姿","Target pose");
    text("#spatial-capture-label","位姿名称","Pose name");
    text("#spatial-home","归位","Home"); text("#spatial-recover","恢复","Recover");
    text("#spatial-capture","记录位姿","Capture pose");
    text("#spatial-can","配置 CAN","Configure CAN"); text("#spatial-can-reset","重置 CAN","Reset CAN");
    text("#spatial-host","本机参数","Host settings");text("#spatial-open-cameras","查看相机","View cameras");
    text("#rail-alert-label","设备状态","Device status");text("#rail-action-label","最近操作","Recent action");
    const poseTitle=$(".device-card[data-card-id=arms] .card-head h3");if(poseTitle)poseTitle.textContent=t("位姿管理","Pose management");
  }
  function render() {
    if(!$("#spatial-layout"))return;
    for(const el of document.querySelectorAll("[data-spatial]")){
      const id=el.dataset.spatial, value=status(id);
      el.dataset.state=value.phase;
      el.classList.toggle("is-focused",id===state.focus);
      el.classList.toggle("is-selected",state.selected.has(id));
      el.setAttribute("aria-pressed",String(state.selected.has(id)));
      el.setAttribute("aria-label",`${name(id)} · ${(phases[value.phase] || phases.unknown)[en()?1:0]}`);
    }
    for(const [side,a,b] of [["left","front-left","rear-left"],["center","mid","computer"],["right","front-right","rear-right"]]){
      const el=$(`[data-link="${side}"]`), p=status(a).phase, q=status(b).phase;
      el.dataset.state=p==="ready"&&q==="ready"?"ready":p==="unknown"||q==="unknown"?"unknown":"error";
    }
    const current=status(state.focus);
    $("#spatial-focus-name").textContent=name(state.focus);
    $("#spatial-focus-phase").textContent=(phases[current.phase] || phases.unknown)[en()?1:0];
    $("#spatial-focus-phase").dataset.state=current.phase;
    $("#spatial-focus-detail").textContent=current.detail;
    $("#spatial-focus-icon").textContent=state.focus==="computer"?"▣":cameraIds.includes(state.focus)?"◉":state.focus.startsWith("gripper")?"⌁":"◈";
    $("#spatial-selection").textContent=state.selected.size?t("已选：","Selected: ")+[...state.selected].map(name).join("、"):"";
    $("#spatial-arm-actions").hidden=state.focus==="computer"||cameraIds.includes(state.focus);
    $("#spatial-computer-actions").hidden=state.focus!=="computer";
    $("#spatial-camera-actions").hidden=!cameraIds.includes(state.focus);
    renderActions();renderHost();renderAlert();
  }
  function renderActions() {
    const target=selectedTarget(), poses=target?state.devices?.home_poses?.[target] || root.CobotDeviceUI?.posesFor(target) || []:[];
    const select=$("#spatial-pose"), previous=select.value;
    select.replaceChildren(...poses.map(pose=>{const option=document.createElement("option");option.value=pose;option.textContent=pose;return option;}));
    if(poses.includes(previous))select.value=previous;
    $("#spatial-home").disabled=state.busy||!target||!poses.length;
    $("#spatial-capture").disabled=state.busy||!target||target==="gripper"||!$("#spatial-capture-name").value.trim();
    $("#spatial-recover").disabled=state.busy||![...state.selected].some(id=>armIds.includes(id)||gripperIds.includes(id));
    $("#spatial-action-limit").textContent=state.selected.size&&!target?
      t("该组合暂无归位或位姿记录入口","No home or pose capture for this selection"):"";
  }
  function home() {
    const target=selectedTarget(), pose=$("#spatial-pose").value;
    if(!target || !pose)return;
    const oldTarget=$("#device-home-target"), oldPose=$("#device-home-pose");
    oldTarget.value=target;oldTarget.dispatchEvent(new Event("change",{bubbles:true}));
    if([...oldPose.options].some(option=>option.value===pose))oldPose.value=pose;
    oldPose.dispatchEvent(new Event("change",{bubbles:true}));
    $("#device-home").click();
  }
  async function capture() {
    const target=selectedTarget(), pose=$("#spatial-capture-name").value.trim();
    if(!target || target==="gripper" || !pose)return;
    const oldTarget=$("#device-home-target");oldTarget.value=target;oldTarget.dispatchEvent(new Event("change",{bubbles:true}));
    $("#device-pose-name").value=pose;
    state.busy=true;renderActions();
    try{await root.CobotDeviceUI.runPose("capture");}
    finally{state.busy=false;renderActions();}
  }
  async function waitRecover(job) {
    for(let n=0;n<100;n++){
      await new Promise(resolve=>setTimeout(resolve,400));
      const response=await fetch("/api/console/devices",{cache:"no-store"});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const current=(await response.json()).jobs?.recover;
      if(current?.job_id!==job.job_id)continue;
      if(current.phase==="completed")return;
      if(["failed","stale","stopped"].includes(current.phase))throw new Error(current.detail||current.phase);
    }
    throw new Error(t("恢复等待超时","Recover timed out"));
  }
  async function recover() {
    const targets=[...state.selected].filter(id=>armIds.includes(id)||gripperIds.includes(id));
    if(!targets.length || state.busy)return;
    state.busy=true;renderActions();
    try{
      for(const target of targets){
        const job=await root.CobotDeviceUI.runRecover(target);
        if(!job)break;
        await waitRecover(job);
      }
    }catch(error){root.CobotWorkspaceUI?.report(`${t("恢复中止","Recover stopped")}：${error.message}`,"error","设备");}
    finally{state.busy=false;renderActions();poll();}
  }
  function renderHost() {
    if(state.focus!=="computer")return;
    const host=state.host, disk=host?.disk, systems=state.devices?.systems || {};
    const lines=[
      [t("主机","Host"),host?.hostname || "—"],
      ["CAN",(phases[systems.can?.phase] || phases.unknown)[en()?1:0]],
      ["ROS",(phases[systems.roscore?.phase] || phases.unknown)[en()?1:0]],
      [t("剩余空间","Free disk"),disk?.free_bytes==null?"—":`${(Number(disk.free_bytes)/1024**3).toFixed(1)} GiB`]
    ];
    const container=$("#spatial-computer-facts");container.replaceChildren();
    for(const [label,value] of lines){const row=document.createElement("div"), key=document.createElement("span"), val=document.createElement("strong");key.textContent=label;val.textContent=value;row.append(key,val);container.append(row);}
  }
  async function refreshHost() {
    try{const response=await fetch("/api/console/host",{cache:"no-store"});if(!response.ok)throw new Error("HTTP "+response.status);state.host=await response.json();state.lastHost=Date.now();renderHost();}
    catch(_error){state.host=null;renderHost();}
  }
  function renderAlert() {
    const systems=state.devices?.systems || {}, feedback=systems.arms_feedback || {};
    const unhealthy=armIds.filter(id=>feedback[id]?.phase!=="ready" || !feedback[id]?.fresh);
    let label, phase;
    if(!state.devices || Date.now()-state.lastDevices>3500){label=t("设备状态未确认","Device status unverified");phase="stale";}
    else if(systems.can?.phase!=="ready"){label=t("CAN 异常","CAN error");phase="error";}
    else if(unhealthy.length){label=t(`${unhealthy.length} 个机械臂反馈异常`,`${unhealthy.length} arm feedback faults`);phase="error";}
    else if(systems.cameras?.phase!=="ready"){label=t("相机异常","Camera error");phase="error";}
    else {label=t("设备正常","Devices ready");phase="ready";}
    const value=$("#rail-alert-value");if(value){value.textContent=label;value.dataset.state=phase;}
  }
  function installRail() {
    const rail=document.createElement("aside");rail.className="status-rail";rail.innerHTML='<div class="rail-card rail-alert"><span id="rail-alert-label"></span><strong id="rail-alert-value"></strong></div><div id="rail-action-slot"></div>';document.body.append(rail);
    const workspace=root.CobotWorkspaceUI;
    if(!workspace || typeof workspace.report!=="function")return;
    const original=workspace.report;
    workspace.report=function(message,tone,source){
      const result=original.apply(this,arguments);
      if(source==="设备"){
        if(/正在请求|已启动|操作未执行/.test(message))state.actionStarted=true;
        if(state.actionStarted)showAction(message,tone);
      } else if(state.actionStarted && source==="机械臂" && /恢复|归位/.test(message))showAction(message,tone);
      return result;
    };
  }
  function showAction(message,tone) {
    const slot=$("#rail-action-slot");if(!slot || !message || message===state.lastAlert)return;
    state.lastAlert=message;
    const old=slot.querySelector(".rail-card:not(.leaving)");
    if(old){old.classList.add("leaving");setTimeout(()=>old.remove(),500);}
    const card=document.createElement("div");card.className="rail-card rail-action";card.dataset.state=tone||"running";
    const label=document.createElement("span");label.id="rail-action-label";label.textContent=t("最近操作","Recent action");
    const value=document.createElement("strong");value.textContent=message;
    card.append(label,value);slot.prepend(card);
    root.setTimeout(()=>{if(card.isConnected){card.classList.add("leaving");root.setTimeout(()=>card.remove(),500);}},9000);
  }
  async function poll() {
    if(state.pollBusy || document.hidden)return;
    state.pollBusy=true;
    try{
      const response=await fetch("/api/console/devices",{cache:"no-store"});
      if(response.ok){state.devices=await response.json();state.lastDevices=Date.now();}
      if(Date.now()-state.lastCameras>500){const cam=await fetch("/api/console/cameras",{cache:"no-store"});if(cam.ok){state.cameras=await cam.json();state.lastCameras=Date.now();}}
      if(state.focus==="computer" && Date.now()-state.lastHost>5000)await refreshHost();
    }catch(_error){}
    finally{state.pollBusy=false;render();}
  }
  root.CobotSpatialUI={mount,selectGroup,selectedTarget};
  if(typeof module!=="undefined"&&module.exports)module.exports={selectedTargetFor:ids=>Object.keys(groups).find(key=>groups[key].length===ids.length&&groups[key].every(id=>ids.includes(id)))||null};
  if(typeof document!=="undefined")document.readyState==="loading"?document.addEventListener("DOMContentLoaded",mount):mount();
})(typeof globalThis!=="undefined"?globalThis:this);
