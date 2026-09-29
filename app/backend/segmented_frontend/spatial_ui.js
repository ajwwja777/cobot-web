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
  const defaultGroups = {
    front: ["front-left", "front-right"], rear: ["rear-left", "rear-right"],
    all: armIds, four: ["front-left", "front-right", "rear-left", "rear-right"],
    mid: ["mid"], gripper: gripperIds
  };
  const phases = {
    teaching: ["示教同步", "Teaching synchronized"], warning: ["需处理", "Attention"], ready: ["正常", "Ready"], error: ["异常", "Error"], disabled: ["失能", "Disabled"], powered: ["已通电", "Powered"],
    stale: ["反馈过期", "Stale"], offline: ["未启动", "Offline"], unknown: ["未核验", "Unknown"]
  };
  const state = {devices:null, cameras:null, host:null, lastDevices:0, lastCameras:0, lastHost:0,
    focus:"front-right", selected:new Set(["front-right"]), busy:false, pollBusy:false, lastAlert:"", actionStarted:false,
    rearModes:{}, rearExitAt:{}, lastJob:"",customGroups:{},activeGroup:null,
    shortcutOrder:[],draggedShortcut:null,suppressShortcutClickUntil:0,captureSelectionKey:null};
  try {
    const saved=JSON.parse(root.localStorage?.getItem("cobot-spatial-groups-v1")||"{}");
    if(saved && typeof saved==="object" && !Array.isArray(saved))
      for(const [key,ids] of Object.entries(saved))
        if(Array.isArray(ids) && ids.length && ids.every(id=>armIds.includes(id)||gripperIds.includes(id)))
          state.customGroups[key]=[...new Set(ids)];
  }catch(_error){}
  try {
    const order=JSON.parse(root.localStorage?.getItem("cobot-spatial-quick-order-v1")||"[]");
    if(Array.isArray(order))state.shortcutOrder=order.filter(key=>typeof key==="string");
  }catch(_error){}
  function en() { return root.CobotPreferences?.language === "en"; }
  function t(zh, english) { return en() ? english : zh; }
  function name(id) { return (names[id] || [id,id])[en() ? 1 : 0]; }
  function shortcutName(key) {
    return key==="all"?t("五臂","Five arms"):key==="four"?t("四臂","Four arms"):key;
  }
  function text(id, zh, english) { const el=$(id); if(el) el.textContent=t(zh,english); }
  function arm(id, x1, y1, x2, y2, x3, y3) {
    return `<g class="spatial-node spatial-arm" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <path class="arm-link" d="M${x1} ${y1} L${x2} ${y2} L${x3} ${y3}"/>
      <circle class="selection-ring" cx="${x1}" cy="${y1}" r="29"/>
      <circle class="arm-base" cx="${x1}" cy="${y1}" r="23"/>
      <circle class="arm-joint" cx="${x2}" cy="${y2}" r="10"/><circle class="arm-tip" cx="${x3}" cy="${y3}" r="10"/></g>`;
  }
  function camera(id, x, y) {
    return `<g class="spatial-node spatial-camera" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <circle class="camera-box" cx="${x}" cy="${y}" r="23"/><circle class="camera-lens" cx="${x}" cy="${y}" r="8"/><circle class="camera-reflection" cx="${x+3}" cy="${y-3}" r="2"/></g>`;
  }
  function gripper(id, x, y) {
    return `<g class="spatial-node spatial-gripper" data-spatial="${id}" role="button" tabindex="0" aria-pressed="false">
      <circle class="selection-ring" cx="${x}" cy="${y}" r="29"/><circle class="gripper-halo" cx="${x}" cy="${y}" r="23"/><path class="gripper-fingers" d="M${x-10} ${y-9}V${y+8}H${x+10}V${y-9}H${x+5}V${y+1}H${x-5}V${y-9}Z"/></g>`;
  }
  function sceneMarkup() {
    return `<div class="spatial-floor"><svg class="spatial-scene" viewBox="0 0 940 480" role="img" aria-label="Cobot component status" preserveAspectRatio="xMidYMid meet">
      <defs><filter id="scene-glow"><feGaussianBlur stdDeviation="7" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>
      <rect class="table-outline" x="35" y="34" width="870" height="412" rx="22"/>
      <path class="table-grid" d="M55 158H885 M55 320H885 M317 50V430 M626 50V430"/>
      <path class="spatial-connection" data-link="left" d="M205 390 L205 250"/>
      <path class="spatial-connection" data-link="right" d="M735 390 L735 250"/>
      <path class="spatial-connection" data-link="center" d="M470 360 L470 250"/>
      ${arm("front-left",205,250,205,170,205,90)}
      ${arm("mid",470,250,470,170,470,90)}
      ${arm("front-right",735,250,735,170,735,90)}
      ${arm("rear-left",205,390,205,350,205,315)}
      ${arm("rear-right",735,390,735,350,735,315)}
      ${camera("camera_left",205,170)}${camera("camera_high",470,170)}${camera("camera_right",735,170)}
      ${gripper("gripper-left",205,90)}${gripper("gripper-right",735,90)}
      <g class="spatial-node spatial-computer" data-spatial="computer" role="button" tabindex="0" aria-pressed="false">
        <circle class="computer-box" cx="470" cy="365" r="30"/><rect class="computer-screen" x="455" y="352" width="30" height="22" rx="3"/><path class="computer-stand" d="M470 374v6m-10 0h20"/></g>
      <text class="spatial-age" x="890" y="438" text-anchor="end"></text>
      </svg></div>`;
  }
  function mount() {
    const panel=$(".infrastructure-panel"), grid=panel?.querySelector(".device-dashboard");
    if(!panel || !grid || $("#spatial-layout")) return;
    const section=document.createElement("section"); section.className="spatial-layout"; section.id="spatial-layout";
    section.innerHTML=`<div class="spatial-card"><header class="spatial-head"><h3 id="spatial-heading"></h3><div class="spatial-quick-groups" id="spatial-quick-groups"></div><button type="button" id="spatial-save-group">保存组合</button></header>${sceneMarkup()}</div><div class="spatial-detail"><div class="spatial-detail-head"><div><h3 id="spatial-focus-name"></h3><span class="spatial-phase" id="spatial-focus-phase"></span></div></div>
      <p class="spatial-detail-value" id="spatial-focus-detail"></p><div class="spatial-selection" id="spatial-selection"></div>
      <div class="spatial-actions" id="spatial-arm-actions"><label><span id="spatial-pose-label"></span><select id="spatial-pose"></select></label>
        <button type="button" id="spatial-home"></button>
        <div id="spatial-pose-recording"><label><span id="spatial-capture-label"></span><input id="spatial-capture-name" list="spatial-pose-names" maxlength="64" autocomplete="off"></label><datalist id="spatial-pose-names"></datalist><fieldset class="spatial-capture-arms" id="spatial-capture-arms"><legend id="spatial-capture-arms-label"></legend></fieldset><div class="spatial-button-row"><button type="button" id="spatial-capture" class="secondary"></button><button type="button" id="spatial-pose-delete" class="danger">删除位姿</button></div><small id="spatial-action-limit"></small><small class="spatial-config-path" id="spatial-config-path"></small></div><button type="button" id="spatial-recover" class="secondary"></button></div>
      <div class="spatial-actions" id="spatial-computer-actions" hidden><div class="spatial-computer-facts" id="spatial-computer-facts"></div><div class="spatial-button-row"><button type="button" id="spatial-can"></button><button type="button" id="spatial-can-reset" class="secondary"></button></div><div class="spatial-button-row"><button type="button" id="spatial-ros-start">启动 ROS</button><button type="button" id="spatial-ros-stop" class="secondary">停止 ROS</button></div><div class="spatial-button-row"><button type="button" id="spatial-arms-start">启动机械臂</button><button type="button" id="spatial-arms-stop" class="secondary">停止机械臂</button></div><div class="spatial-button-row"><button type="button" id="spatial-cameras-start">启动相机</button><button type="button" id="spatial-cameras-stop" class="secondary">停止相机</button></div><button type="button" id="spatial-host" class="secondary"></button></div>
      <div class="spatial-actions" id="spatial-camera-actions" hidden><div id="spatial-camera-facts"></div><button type="button" id="spatial-open-cameras" class="secondary"></button></div>
    </div>`;
    grid.before(section);
    const poseCard=grid.querySelector('[data-card-id="arms"]');
    if(poseCard){ poseCard.querySelector(".card-head h3").textContent=t("位姿管理","Pose management"); poseCard.querySelector(".physical-arm-map")?.classList.add("hidden"); }
    for(const item of section.querySelectorAll("[data-spatial]")){
      item.addEventListener("click",event=>{if(Date.now()<(state.suppressSceneClickUntil||0))return;choose(item.dataset.spatial,event.ctrlKey||event.metaKey);});
      item.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();choose(item.dataset.spatial,event.ctrlKey||event.metaKey);}});
    }
    $("#spatial-save-group").addEventListener("click",saveShortcut);
    $("#spatial-home").addEventListener("click",home);
    $("#spatial-recover").addEventListener("click",recover);
    $("#spatial-capture").addEventListener("click",capture);
    $("#spatial-pose-delete").addEventListener("click",deletePose);
    $("#spatial-pose").addEventListener("change",()=>{$("#spatial-capture-name").value=$("#spatial-pose").value;renderActions();});
    for(const arm of armIds){
      const label=document.createElement("label"),box=document.createElement("input"),caption=document.createElement("span");
      box.type="checkbox";box.value=arm;caption.dataset.armName=arm;label.append(box,caption);
      $("#spatial-capture-arms").append(label);box.addEventListener("change",renderActions);
    }
    $("#spatial-capture-name").addEventListener("input",renderActions);
    $("#spatial-can").addEventListener("click",()=>$("#device-can")?.click());
    $("#spatial-can-reset").addEventListener("click",()=>$("#device-can-reset")?.click());
    $("#spatial-host").addEventListener("click",()=>$(".nav-item[data-view=host]")?.click());
    $("#spatial-open-cameras").addEventListener("click",()=>$("#camera-nav")?.click());
    for(const [id,target] of [["ros-start","device-roscore"],["ros-stop","device-roscore-stop"],["arms-start","device-arms"],["arms-stop","device-arms-stop"],["cameras-start","device-cameras"],["cameras-stop","device-cameras-stop"]])
      $("#spatial-"+id)?.addEventListener("click",()=>$("#"+target)?.click());
    installMarquee(section.querySelector(".spatial-scene"));
    installRail(); localize(); render();
    document.addEventListener("cobot:language",()=>{ localize(); render(); });
    root.setInterval(poll,500);
    root.setInterval(renderAge,100);
    poll();
  }
  function selectionInBox(points, box, previous=[], additive=false) {
    const found=Object.entries(points).filter(([,p])=>p.x>=box.left&&p.x<=box.right&&p.y>=box.top&&p.y<=box.bottom).map(([id])=>id);
    return [...new Set([...(additive?previous.filter(id=>armIds.includes(id)):[]),...found])];
  }
  function installMarquee(svg) {
    let drag=null;
    const rect=document.createElementNS("http://www.w3.org/2000/svg","rect");
    rect.classList.add("spatial-marquee");rect.setAttribute("visibility","hidden");svg.append(rect);
    const point=event=>{
      const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;
      const matrix=svg.getScreenCTM();return matrix?p.matrixTransform(matrix.inverse()):null;
    };
    const clear=()=>{rect.setAttribute("visibility","hidden");for(const el of svg.querySelectorAll(".is-box-preview"))el.classList.remove("is-box-preview");};
    svg.addEventListener("pointerdown",event=>{
      if(event.button!==0||!event.isPrimary)return;
      const p=point(event);if(!p)return;
      drag={id:event.pointerId,start:p,x:event.clientX,y:event.clientY,additive:event.ctrlKey||event.metaKey,previous:[...state.selected],active:false,ids:[]};
    });
    svg.addEventListener("pointermove",event=>{
      if(!drag||event.pointerId!==drag.id)return;
      if(!drag.active&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<6)return;
      const p=point(event);if(!p)return;
      if(!drag.active){drag.active=true;svg.setPointerCapture(event.pointerId);}
      event.preventDefault();
      const box={left:Math.min(p.x,drag.start.x),right:Math.max(p.x,drag.start.x),top:Math.min(p.y,drag.start.y),bottom:Math.max(p.y,drag.start.y)};
      for(const [key,value] of Object.entries({x:box.left,y:box.top,width:box.right-box.left,height:box.bottom-box.top,visibility:"visible"}))rect.setAttribute(key,String(value));
      const points=Object.fromEntries([...svg.querySelectorAll(".spatial-arm")].map(el=>{const base=el.querySelector(".arm-base");return [el.dataset.spatial,{x:Number(base.getAttribute("cx")),y:Number(base.getAttribute("cy"))}];}));
      drag.ids=selectionInBox(points,box,drag.previous,drag.additive);
      for(const el of svg.querySelectorAll(".spatial-arm"))el.classList.toggle("is-box-preview",drag.ids.includes(el.dataset.spatial));
    });
    const finish=(event,cancel=false)=>{
      if(!drag||event.pointerId!==drag.id)return;
      const completed=drag;drag=null;clear();
      if(completed.active){
        state.suppressSceneClickUntil=Date.now()+300;
        if(!cancel){state.selected=new Set(completed.ids);state.activeGroup=null;state.focus=completed.ids[0]||"front-right";render();}
        if(svg.hasPointerCapture(event.pointerId))svg.releasePointerCapture(event.pointerId);
      }
    };
    svg.addEventListener("pointerup",event=>finish(event));
    svg.addEventListener("pointercancel",event=>finish(event,true));
    svg.addEventListener("lostpointercapture",event=>finish(event,true));
    svg.addEventListener("pointerleave",()=>{if(drag&&!drag.active){drag=null;clear();}});
  }
  function choose(id, additive=false) {
    state.activeGroup=null;
    const selectable=armIds.includes(id)?armIds:gripperIds.includes(id)?gripperIds:null;
    if(!selectable){
      state.selected.clear();
    }else if(additive){
      if([...state.selected].some(item=>!selectable.includes(item)))state.selected.clear();
      if(state.selected.has(id))state.selected.delete(id);
      else state.selected.add(id);
    }else state.selected=new Set([id]);
    state.focus=state.selected.size && !state.selected.has(id)?[...state.selected].at(-1):id;
    if(id==="computer")refreshHost();
    render();
  }
  function selectShortcut(key) {
    if(Date.now()<state.suppressShortcutClickUntil)return;
    const ids=key==="all"||key==="four"?defaultGroups[key]:state.customGroups[key];
    if(!ids?.length)return;
    state.selected=new Set(ids);
    state.focus=ids[0];
    state.activeGroup=key;
    render();
  }
  function renderShortcuts() {
    const host=$("#spatial-quick-groups");if(!host)return;
    host.replaceChildren();
    const keys=["all","four",...Object.keys(state.customGroups).filter(key=>key!=="all"&&key!=="four")];
    keys.sort((a,b)=>{
      const ai=state.shortcutOrder.indexOf(a),bi=state.shortcutOrder.indexOf(b);
      return ai<0&&bi<0?0:ai<0?1:bi<0?-1:ai-bi;
    });
    for(const key of keys){
      const builtin=key==="all"||key==="four",ids=builtin?defaultGroups[key]:state.customGroups[key];
      const chip=document.createElement("span");chip.className="spatial-quick-chip";
      chip.dataset.group=key;chip.draggable=true;chip.title=t("拖动排序","Drag to reorder");
      chip.addEventListener("dragstart",event=>{
        state.draggedShortcut=key;chip.classList.add("is-dragging");
        event.dataTransfer.effectAllowed="move";event.dataTransfer.setData("text/plain",key);
      });
      chip.addEventListener("dragover",event=>{
        if(!state.draggedShortcut||state.draggedShortcut===key)return;
        event.preventDefault();event.dataTransfer.dropEffect="move";
        chip.dataset.dropSide=event.clientX<chip.getBoundingClientRect().left+chip.getBoundingClientRect().width/2?"before":"after";
      });
      chip.addEventListener("dragleave",()=>delete chip.dataset.dropSide);
      chip.addEventListener("drop",event=>{
        event.preventDefault();event.stopPropagation();
        const dragged=state.draggedShortcut;
        if(!dragged||dragged===key)return;
        const order=keys.filter(id=>id!==dragged);
        order.splice(order.indexOf(key)+(chip.dataset.dropSide==="after"?1:0),0,dragged);
        state.shortcutOrder=order;state.suppressShortcutClickUntil=Date.now()+300;
        try{root.localStorage?.setItem("cobot-spatial-quick-order-v1",JSON.stringify(order));}catch(_error){}
        renderShortcuts();render();
      });
      chip.addEventListener("dragend",()=>{
        state.draggedShortcut=null;
        for(const item of host.children){item.classList.remove("is-dragging");delete item.dataset.dropSide;}
      });
      const button=document.createElement("button");button.type="button";button.className="spatial-quick-select";
      button.dataset.group=key;button.textContent=shortcutName(key);
      button.title=ids.map(name).join("、");button.addEventListener("click",()=>selectShortcut(key));chip.append(button);
      if(!builtin){
        const remove=document.createElement("button");remove.type="button";remove.className="spatial-quick-remove";
        remove.textContent="×";remove.title=t("删除组合","Delete group");
        remove.addEventListener("click",()=>{
          if(!root.confirm(t(`删除组合 ${key}？`,`Delete group ${key}?`)))return;
          delete state.customGroups[key];
          if(state.activeGroup===key)state.activeGroup=null;
          state.shortcutOrder=state.shortcutOrder.filter(id=>id!==key);
          try{root.localStorage?.setItem("cobot-spatial-groups-v1",JSON.stringify(state.customGroups));root.localStorage?.setItem("cobot-spatial-quick-order-v1",JSON.stringify(state.shortcutOrder));}catch(_error){}
          renderShortcuts();render();
        });chip.append(remove);
      }
      host.append(chip);
    }
  }
  function saveShortcut() {
    const ids=[...state.selected];if(ids.length<2)return;
    const suggestion=t(`组合 ${Object.keys(state.customGroups).length+1}`,`Group ${Object.keys(state.customGroups).length+1}`);
    const entered=root.prompt(t("组合名称","Group name"),suggestion);
    if(entered===null)return;
    const key=entered.trim().slice(0,24);
    if(!key)return;
    if(key in defaultGroups || Object.hasOwn(state.customGroups,key)){
      root.alert(t("组合名称已存在","Group name already exists"));return;
    }
    state.customGroups[key]=ids;state.activeGroup=key;
    try{root.localStorage?.setItem("cobot-spatial-groups-v1",JSON.stringify(state.customGroups));}catch(_error){}
    renderShortcuts();render();
  }
  function selectedTarget() {
    const ids=[...state.selected];
    return Object.keys(defaultGroups).find(key=>defaultGroups[key].length===ids.length && defaultGroups[key].every(id=>state.selected.has(id))) || null;
  }
  function status(id) {
    if(id==="computer"){
      const can=state.devices?.systems?.can, ros=state.devices?.systems?.roscore;
      return {phase:!state.devices?'unknown':can?.phase==='ready'&&ros?.phase==='ready'?'ready':'error',
        detail:!state.devices?t("等待状态","Waiting for status"):`CAN ${(phases[can?.phase] || phases.unknown)[en()?1:0]} (${can?.detail||"—"}) · ROS ${(phases[ros?.phase] || phases.unknown)[en()?1:0]} (${ros?.detail||"—"})`};
    }
    if(cameraIds.includes(id)){
      const camera=state.cameras, age=camera?.age_sec?.[id];
      const stale=!camera || Date.now()-state.lastCameras>2500 || camera.stale_keys?.includes(id) || typeof age!=="number" || age>1.2;
      const node=state.devices?.systems?.camera_nodes?.[id];
      return {phase:!camera?'unknown':!node||stale?'error':camera.status==='ready'?'ready':'error',
        detail:stale?t("画面未更新","Video not updating"):`${Number(camera.preview_fps || 0).toFixed(1)} FPS · ${Math.round(age*1000)} ms`};
    }
    if(!state.devices)return {phase:"unknown",detail:t("等待状态","Waiting for status")};
    if(Date.now()-state.lastDevices>3500)return {phase:"stale",detail:t("反馈已过期","Feedback stale")};
    const health=state.devices.systems?.device_health?.[id];
    if(health){
      const reason=health[en()?"reason_en":"reason_zh"], remedy=health[en()?"remedy_en":"remedy_zh"];
      return {phase:health.phase,detail:[reason,remedy,health.detail].filter(Boolean).join(" · ")};
    }
    return {phase:"unknown",detail:t("等待设备健康检查","Waiting for device health check")};
  }

  function updateRearTransitions(){
    const homeStarted=Number(state.devices?.jobs?.home?.started_at||0)*1000;
    for(const id of ["rear-left","rear-right"]){
      const current=state.devices?.systems?.arms_feedback?.[id]?.rear_mode;
      if(!current)continue;
      if(state.rearModes[id]==="teaching" && current!=="teaching")state.rearExitAt[id]=Date.now();
      if(current==="idle_disabled" || current==="teaching")delete state.rearExitAt[id];
      if(state.rearExitAt[id] && homeStarted>state.rearExitAt[id])delete state.rearExitAt[id];
      state.rearModes[id]=current;
    }
  }
  function localize() {
    $(".spatial-scene")?.setAttribute("aria-label",t("Cobot 部件状态","Cobot component status"));
    text("#spatial-heading","设备","Devices");
    text("#spatial-save-group","保存组合","Save group");
    renderShortcuts();
    for(const el of document.querySelectorAll("[data-name]"))el.textContent=name(el.dataset.name);
    text("#spatial-pose-label","目标位姿","Target pose");
    text("#spatial-capture-label","位姿名称","Pose name");
    text("#spatial-home","归位","Home"); text("#spatial-recover","恢复","Recover");
    text("#spatial-capture","记录位姿","Capture pose");
    text("#spatial-capture-arms-label","记录哪些臂","Record arms");
    text("#spatial-can","配置 CAN","Configure CAN"); text("#spatial-can-reset","重置 CAN","Reset CAN");
    text("#spatial-host","本机参数","Host settings");text("#spatial-open-cameras","查看相机","View cameras");
    text("#spatial-pose-delete","删除位姿","Delete pose");
    text("#spatial-ros-start","启动 ROS","Start ROS");text("#spatial-ros-stop","停止 ROS","Stop ROS");
    text("#spatial-arms-start","启动机械臂","Start arms");text("#spatial-arms-stop","停止机械臂","Stop arms");
    text("#spatial-cameras-start","启动相机","Start cameras");text("#spatial-cameras-stop","停止相机","Stop cameras");
    text("#rail-alert-label","设备状态","Device status");text("#rail-action-label","最近操作","Recent action");
    const poseTitle=$(".device-card[data-card-id=arms] .card-head h3");if(poseTitle)poseTitle.textContent=t("位姿管理","Pose management");
  }
  function render() {
    if(!$("#spatial-layout"))return;
    for(const el of document.querySelectorAll("[data-spatial]")){
      const id=el.dataset.spatial, value=status(id);
      el.dataset.state=value.phase;
      el.classList.toggle("is-focused",id===state.focus && (state.selected.has(id)||!armIds.includes(id)&&!gripperIds.includes(id)));
      el.classList.toggle("is-selected",state.selected.has(id));
      el.setAttribute("aria-pressed",String(state.selected.has(id)));
      el.setAttribute("aria-label",`${name(id)} · ${(phases[value.phase] || phases.unknown)[en()?1:0]}`);
      let title=el.querySelector("title");if(!title){title=document.createElementNS("http://www.w3.org/2000/svg","title");el.prepend(title);}
      title.textContent=`${name(id)} · ${(phases[value.phase] || phases.unknown)[en()?1:0]} · ${value.detail}`;
    }
    for(const [side,a,b] of [["left","front-left","rear-left"],["center","mid","computer"],["right","front-right","rear-right"]]){
      const el=$(`[data-link="${side}"]`), p=status(a).phase, q=status(b).phase;
      el.dataset.state=p==="teaching"&&q==="teaching"?"teaching":p==="ready"&&q==="ready"?"ready":p==="unknown"||q==="unknown"?"unknown":"warning";
    }
    const current=status(state.focus),multiple=state.selected.size>1,shortcut=!!state.activeGroup;
    const empty=!state.selected.size && state.focus!=="computer" && !cameraIds.includes(state.focus);
    const label=shortcut?shortcutName(state.activeGroup):multiple?(state.selected.size===2 && selectedTarget()==="gripper"?t("双夹爪","Both grippers"):
      t(`已选 ${state.selected.size} 臂`,`${state.selected.size} arms selected`)):state.selected.size?name(state.focus):
      cameraIds.includes(state.focus)||state.focus==="computer"?name(state.focus):t("未选择","Nothing selected");
    $("#spatial-focus-name").textContent=label;
    $("#spatial-focus-phase").hidden=shortcut||multiple||empty;
    $("#spatial-focus-detail").hidden=shortcut||multiple||empty;
    if(!$("#spatial-focus-phase").hidden){
      $("#spatial-focus-phase").textContent=(phases[current.phase] || phases.unknown)[en()?1:0];
      $("#spatial-focus-phase").dataset.state=current.phase;
      $("#spatial-focus-detail").textContent=current.detail;
    }
    $("#spatial-selection").textContent=shortcut||multiple?t("已选：","Selected: ")+[...state.selected].map(name).join("、"):"";
    $("#spatial-save-group").disabled=state.selected.size<2;
    for(const button of document.querySelectorAll(".spatial-quick-select")){
      const active=button.dataset.group===state.activeGroup;
      button.classList.toggle("is-active",active);button.setAttribute("aria-pressed",String(active));
    }
    renderAge();
    if(cameraIds.includes(state.focus))$("#spatial-camera-facts").textContent=status(state.focus).detail;
    $("#spatial-arm-actions").hidden=state.focus==="computer"||cameraIds.includes(state.focus);
    $("#spatial-computer-actions").hidden=state.focus!=="computer";
    $("#spatial-camera-actions").hidden=!cameraIds.includes(state.focus);
    renderActions();renderHost();renderAlert();
    root.CobotFeaturePaths?.update("devices",{focus:state.focus,devices:state.devices});
  }
  function renderActions() {
    const target=selectedTarget(),ids=[...state.selected].filter(id=>armIds.includes(id)),available=state.devices?.home_poses||{};
    const grips=[...state.selected].filter(id=>gripperIds.includes(id));
    const poses=grips.length?["reinit"]:ids.length?ids.reduce((shared,id)=>shared.filter(pose=>(available[id]||[]).includes(pose)),available[ids[0]]||[]):[];
    const select=$("#spatial-pose"), previous=select.value;
    if(JSON.stringify([...select.options].map(option=>option.value))!==JSON.stringify(poses))
      select.replaceChildren(...poses.map(pose=>{const option=document.createElement("option");option.value=pose;option.textContent=pose;return option;}));
    if(poses.includes(previous))select.value=previous;
    $("#spatial-home").disabled=state.busy||!poses.length;
    const selectionKey=ids.join(",");
    if(state.captureSelectionKey!==selectionKey){
      state.captureSelectionKey=selectionKey;
      for(const box of document.querySelectorAll("#spatial-capture-arms input"))box.checked=ids.includes(box.value);
    }
    for(const caption of document.querySelectorAll("#spatial-capture-arms [data-arm-name]"))caption.textContent=name(caption.dataset.armName);
    $("#spatial-pose-recording").hidden=!ids.length;
    const names=[...new Set(Object.values(available).flat())].filter(p=>p!=="reinit").sort();
    const suggestions=$("#spatial-pose-names");
    if(suggestions.dataset.names!==JSON.stringify(names)){
      suggestions.dataset.names=JSON.stringify(names);
      suggestions.replaceChildren(...names.map(p=>{const option=document.createElement("option");option.value=p;return option;}));
    }
    $("#spatial-capture").textContent=names.includes($("#spatial-capture-name").value.trim())?t("替换所选臂位姿","Replace selected arm poses"):t("记录新位姿","Capture new pose");
    $("#spatial-capture-name").placeholder=t("选择已有名称或输入新名称","Choose an existing name or enter a new one");
    $("#spatial-capture").disabled=state.busy||!document.querySelector("#spatial-capture-arms input:checked")||!$("#spatial-capture-name").value.trim();
    $("#spatial-pose-delete").disabled=state.busy||!ids.length||!select.value;
    $("#spatial-recover").textContent=grips.length===1?t("恢复所选夹爪","Recover selected gripper"):grips.length===2?t("恢复双夹爪","Recover both grippers"):t("恢复所选机械臂","Recover selected arms");
    $("#spatial-home").textContent=grips.length?t("张开 / 闭合所选夹爪","Open / close selected grippers"):t("归位","Home");
    $("#spatial-recover").disabled=state.busy||![...state.selected].some(id=>armIds.includes(id)||gripperIds.includes(id));
    $("#spatial-config-path").textContent=state.devices?.pose_config_path||"";
    $("#spatial-action-limit").textContent=ids.length&&!poses.length?
      t("此组合尚无共用位姿；可选择臂并记录新位姿。","No shared pose yet; select arms and capture a new pose."):"";
  }
  async function home() {
    const target=selectedTarget(), pose=$("#spatial-pose").value;
    if(!pose || state.busy)return;
    const grips=[...state.selected].filter(id=>gripperIds.includes(id));
    if(grips.length){
      state.busy=true;renderActions();
      try{await root.CobotDeviceUI.execute({component:"home",action:"run",target:grips.length===2?"gripper":grips[0],pose:"reinit"},t("所选夹爪张开后闭合","Open and close selected grippers"));}
      finally{state.busy=false;renderActions();}
      return;
    }
    const arms=[...state.selected].filter(id=>armIds.includes(id));if(!arms.length)return;
    state.busy=true;renderActions();
    try{await root.CobotDeviceUI.runSelection("home",arms,pose);}
    finally{state.busy=false;renderActions();}
  }
  async function capture() {
    const arms=[...document.querySelectorAll("#spatial-capture-arms input:checked")].map(box=>box.value),pose=$("#spatial-capture-name").value.trim();
    if(!arms.length || !pose)return;
    state.busy=true;renderActions();
    try{const job=await root.CobotDeviceUI.runSelection("capture",arms,pose);
      if(job){const done=await root.CobotDeviceUI.waitForJob(job);if(done.current.phase==="completed"){state.devices=done.payload;render();if([...$("#spatial-pose").options].some(option=>option.value===pose))$("#spatial-pose").value=pose;}}
    }catch(error){root.CobotWorkspaceUI?.report(`Pose capture failed: ${error.message}`,"error","设备");}
    finally{state.busy=false;renderActions();}
  }
  async function deletePose(){
    const arms=[...state.selected].filter(id=>armIds.includes(id)),pose=$("#spatial-pose").value;
    if(!arms.length||!pose||state.busy)return;
    state.busy=true;renderActions();
    try{const job=await root.CobotDeviceUI.runSelection("delete",arms,pose);
      if(job){const done=await root.CobotDeviceUI.waitForJob(job);if(done.current.phase==="completed"){state.devices=done.payload;render();}}
    }catch(error){root.CobotWorkspaceUI?.report(`${t("删除位姿失败","Pose deletion failed")}：${error.message}`,"error","设备");}
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
      const batch=targets.includes("front-left")&&targets.includes("front-right")?
        ["front-pair",...targets.filter(id=>id!=="front-left"&&id!=="front-right")]:targets;
      for(const target of batch){
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
      [t("工控机剩余空间","Computer free disk"),disk?.free_bytes==null?"—":`${(Number(disk.free_bytes)/1024**3).toFixed(1)} GiB`],
      [t("当前模型","Active model"),$("#dashboard-model")?.textContent || $("#deployment-model")?.selectedOptions?.[0]?.textContent || "—"]
    ];
    const container=$("#spatial-computer-facts");container.replaceChildren();
    for(const [label,value] of lines){const row=document.createElement("div"), key=document.createElement("span"), val=document.createElement("strong");key.textContent=label;val.textContent=value;
      if(label==="CAN"||label==="ROS"){
        const dot=document.createElement("i");dot.className="system-ready-dot";
        dot.dataset.state=(label==="CAN"?systems.can:systems.roscore)?.phase||"unknown";
        dot.setAttribute("aria-hidden","true");val.prepend(dot);
      }
      row.append(key,val);container.append(row);}
  }
  async function refreshHost() {
    try{const response=await fetch("/api/console/host",{cache:"no-store"});if(!response.ok)throw new Error("HTTP "+response.status);state.host=await response.json();state.lastHost=Date.now();renderHost();}
    catch(_error){state.host=null;renderHost();}
  }
  function renderAlert() {
    const systems=state.devices?.systems || {}, feedback=systems.arms_feedback || {};
    const unhealthy=armIds.filter(id=>["error","warning","offline","stale","unknown"].includes(status(id).phase));
    let label, phase;
    if(!state.devices || Date.now()-state.lastDevices>3500){label=t("设备状态未确认","Device status unverified");phase="stale";}
    else if(systems.can?.phase!=="ready"){label=t("CAN 异常","CAN error");phase="error";}
    else if(systems.roscore?.phase!=="ready"){label=t("ROS Core 未连接","ROS Core offline");phase="error";}
    else if(unhealthy.length){label=t(`${unhealthy.length} 个机械臂反馈异常`,`${unhealthy.length} arm feedback faults`);phase="error";}
    else if(systems.cameras?.phase!=="ready"){label=t("相机异常","Camera error");phase="error";}
    else {label=t("设备正常","Devices ready");phase="ready";}
    const value=$("#rail-alert-value");if(value){value.textContent=label;value.dataset.state=phase;}
    const age=$("#rail-alert-age");if(age)age.textContent=$(".spatial-age")?.textContent||"";
    const card=$(".rail-alert");if(card){card.dataset.state=phase;card.setAttribute("aria-label",label);card.title=label;}
  }
  function renderAge(){
    const label=$(".spatial-age"),rail=$("#rail-alert-age");if(!label)return;
    const observed=Number(state.devices?.systems_observed_at);
    if(!Number.isFinite(observed)||observed<=0||!state.lastDevices){label.textContent=t("等待状态","Waiting");}
    else{
      const elapsed=Math.max(0,Date.now()-state.lastDevices);
      const clock=new Date(observed*1000).toLocaleTimeString(en()?"en-US":"zh-CN",{hour12:false,hour:"2-digit",minute:"2-digit",second:"2-digit",fractionalSecondDigits:3});
      label.textContent=`${clock} · ${elapsed<1000?elapsed+" ms":(elapsed/1000).toFixed(1)+(en()?" s ago":" 秒前")}`;
    }
    if(rail)rail.textContent=label.textContent;
    const card=$(".rail-alert"),value=$("#rail-alert-value");
    if(card&&value){const hint=`${value.textContent} · ${label.textContent}`;card.title=hint;card.setAttribute("aria-label",hint);}
  }
  function installRail() {
    const rail=document.createElement("aside");rail.className="status-rail";rail.innerHTML='<div class="rail-card rail-alert" role="status" tabindex="0" title="设备状态"><span id="rail-alert-label"></span><strong id="rail-alert-value"></strong><small id="rail-alert-age"></small></div><div id="rail-action-slot"><div class="rail-card rail-action" role="status" tabindex="0" title="最近操作 · 尚无操作"><span id="rail-action-label">最近操作</span><strong>尚无操作</strong></div></div>';document.body.append(rail);
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
    const card=document.createElement("div");card.className="rail-card rail-action";card.dataset.state=tone||"running";card.tabIndex=0;card.setAttribute("role","status");card.title=`${t("最近操作","Recent action")} · ${message}`;card.setAttribute("aria-label",card.title);
    const label=document.createElement("span");label.id="rail-action-label";label.textContent=t("最近操作","Recent action");
    const value=document.createElement("strong");value.textContent=message;
    card.append(label,value);slot.prepend(card);
    // Keep the latest operation visible until a newer operation replaces it.
  }
  async function poll() {
    if(state.pollBusy)return;
    state.pollBusy=true;
    try{
      const response=await fetch("/api/console/devices",{cache:"no-store"});
      if(response.ok){
        state.devices=await response.json();state.lastDevices=Date.now();updateRearTransitions();
        const active=Object.values(state.devices.jobs||{}).filter(job=>job?.job_id).sort((a,b)=>Number(b.started_at||0)-Number(a.started_at||0))[0];
        if(active&&(Date.now()/1000-Number(active.started_at||0)<30||state.lastJob.startsWith(active.job_id+":"))){const marker=active.job_id+":"+active.phase;if(marker!==state.lastJob){
          state.lastJob=marker;showAction(`${active.component||"设备"} · ${active.phase}${active.detail?" · "+active.detail:""}`,
            ["failed","stale"].includes(active.phase)?"error":["running","stopping"].includes(active.phase)?"running":"success");
        }}
      }
      if(Date.now()-state.lastCameras>500){const cam=await fetch("/api/console/cameras",{cache:"no-store"});if(cam.ok){state.cameras=await cam.json();state.lastCameras=Date.now();}}
      if(state.focus==="computer" && Date.now()-state.lastHost>5000)await refreshHost();
    }catch(_error){}
    finally{state.pollBusy=false;render();}
  }
  root.CobotSpatialUI={mount,selectedTarget,getSelection:()=>[...state.selected]};
  if(typeof module!=="undefined"&&module.exports)module.exports={selectionInBox,selectedTargetFor:ids=>Object.keys(defaultGroups).find(key=>defaultGroups[key].length===ids.length&&defaultGroups[key].every(id=>ids.includes(id)))||null};
  if(typeof document!=="undefined")document.readyState==="loading"?document.addEventListener("DOMContentLoaded",mount):mount();
})(typeof globalThis!=="undefined"?globalThis:this);
