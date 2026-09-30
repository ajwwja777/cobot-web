"use strict";
(function expose(root) {
  const KEY = "cobot-console-layout-v2";
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const positions = ["top", "right", "bottom", "left"];
  const initial = {cameraOpen:true,cameraPinned:false,cameraEdge:"top",cameraTopSize:300,cameraSideSize:300,logCorner:"bottom-right",orders:{}};
  let layout = initial;
  let mounted = false;
  let lastEvent = "";
  let eventCount = 0;
  let jobs = {};
  let selectedJob = "";
  let cameraDock = null;
  let logDock = null;
  let cameraResizeGuard = 0;
  let activeModel = null;
  let hostBusy = false;

  function readLayout() {
    try {
      const parsed=JSON.parse(root.localStorage.getItem(KEY)||"{}");
      return {...initial,...parsed,orders:{...initial.orders,...(parsed.orders||{})}};
    } catch (_error) { return {...initial}; }
  }
  function saveLayout() {
    try { root.localStorage.setItem(KEY,JSON.stringify(layout)); } catch (_error) {}
  }
  function element(tag,className,text) {
    const node=document.createElement(tag);
    if(className)node.className=className;
    if(text!=null)node.textContent=text;
    return node;
  }
  function row(parent,nodes) { const line=element("div","device-control-stack");for(const node of nodes)if(node)line.append(node);parent.append(line);return line; }
  function move(selector,parent) { const node=$(selector);if(node)parent.append(node);return node; }
  function card(id,title) {
    const article=element("article","panel layout-card device-card");article.dataset.cardId=id;
    const head=element("div","card-head");head.append(element("h3","",title),element("span","card-drag-hint","拖动排序"));
    article.append(head);return article;
  }
  function moveLifecycle(id,parent) {
    const button=$("#"+id);if(!button)return;
    const wrapper=button.closest(".device-lifecycle-row");
    row(parent,[wrapper||button]);
  }
  function statusNode(id,parent) { const node=$("#"+id);if(node)parent.append(node); }

  function physicalArmCell(name,statusIds,recoveryTargets) {
    const cell=element("div","physical-arm-cell");cell.dataset.arm=name;
    for(const id of statusIds)statusNode(id,cell);
    for(const target of recoveryTargets){
      const button=element("button","secondary",target.startsWith("gripper-")?"恢复夹爪":"恢复机械臂");
      button.type="button";button.dataset.recoverTarget=target;
      button.addEventListener("click",()=>root.CobotDeviceUI.runRecover(target));
      cell.append(button);
    }
    return cell;
  }
  function buildSystem() {
    const panel=$(".infrastructure-panel");if(!panel)return;
    const grid=element("div","device-dashboard layout-grid");grid.dataset.layoutGroup="system";
    const basics=card("basics","基础连接"),services=card("services","节点服务");
    const arms=card("arms","五臂与夹爪"),model=card("model","模型与任务");
    const bstatus=element("div","device-card-status");statusNode("dev-can",bstatus);statusNode("dev-roscore",bstatus);basics.append(bstatus);
    moveLifecycle("device-roscore",basics);moveLifecycle("device-can",basics);
    const sstatus=element("div","device-card-status");statusNode("dev-arms",sstatus);statusNode("dev-cameras",sstatus);services.append(sstatus);
    moveLifecycle("device-arms",services);moveLifecycle("device-cameras",services);

    const map=element("div","physical-arm-map");
    map.append(
      physicalArmCell("front-left",["dev-gripper-left","dev-front-left"],["gripper-left","front-left"]),
      physicalArmCell("mid",["dev-mid"],["mid"]),
      physicalArmCell("front-right",["dev-gripper-right","dev-front-right"],["gripper-right","front-right"]),
      physicalArmCell("rear-left",["dev-rear-left"],["rear-left"]),
      element("div","physical-arm-cell arm-center-spacer"),
      physicalArmCell("rear-right",["dev-rear-right"],["rear-right"])
    );arms.append(map);
    const home=element("div","home-controls");home.append(element("h4","","归位 / Pose"));
    row(home,[move("#device-home-target",home),move("#device-home-pose",home)]);
    move("#device-home",home);
    const pose=element("details","pose-management");pose.append(element("summary","","记录 / 删除位姿"));
    for(const id of ["device-pose-name","device-pose-capture","device-pose-delete"])move("#"+id,pose);
    home.append(pose);arms.append(home);
    const legacyRecover=element("div","hidden");move("#device-recover-target",legacyRecover);move("#device-recover",legacyRecover);arms.append(legacyRecover);

    const modelState=element("div","model-state-stack");
    modelState.innerHTML='<span id="dashboard-rlt" data-tone="gray">RLT · 未核验</span><strong id="dashboard-model">模型未加载</strong><small id="dashboard-actor">Actor —</small><small id="dashboard-run">等待现场状态</small>';
    model.append(modelState);
    const stages=element("div","model-loading");stages.id="model-loading";
    for(const label of ["模型恢复","环境连接","待 Arm"]){const stage=element("span","",label);stages.append(stage);}
    model.append(stages);
    const modelLinks=element("div","device-control-stack");
    const training=element("button","secondary","查看训练指标"),deployment=element("button","secondary","查看部署模型");
    training.type=deployment.type="button";
    training.addEventListener("click",()=>$(".nav-item[data-view=training]").click());
    deployment.addEventListener("click",()=>$(".nav-item[data-view=deployment]").click());
    modelLinks.append(training,deployment);model.append(modelLinks);
    grid.append(basics,services,arms,model);
    const head=panel.querySelector(".panel-head");head.after(grid);
    const event=$("#device-health-event");if(event)event.classList.add("hidden");
    const actions=panel.querySelector(".device-actions");if(actions)actions.classList.add("hidden");
    const oldStatuses=panel.querySelector(".device-status-grid");if(oldStatuses)oldStatuses.classList.add("hidden");
    const oldLog=$("#device-log-details");if(oldLog)oldLog.classList.add("hidden");
    enableDragGrid(grid);
  }

  function cameraVisible() { return Boolean(layout.cameraOpen&&cameraDock&&!cameraDock.hidden); }
  function applyCameraLayout() {
    if(!cameraDock)return;
    const edge=positions.includes(layout.cameraEdge)?layout.cameraEdge:"top";
    cameraDock.dataset.edge=edge;cameraDock.hidden=!layout.cameraOpen;
    cameraDock.classList.toggle("is-pinned",Boolean(layout.cameraPinned));
    const pin=$("#camera-pin");if(pin){pin.classList.toggle("selected",Boolean(layout.cameraPinned));pin.setAttribute("aria-pressed",String(Boolean(layout.cameraPinned)));}
    const side=edge==="left"||edge==="right";
    cameraDock.style.setProperty("--camera-size",Math.max(220,Math.min(650,Number(side?layout.cameraSideSize:layout.cameraTopSize)||300))+"px");
    const main=$("main");main.dataset.cameraDock=cameraVisible()?edge:"closed";
    main.style.setProperty("--camera-side-size",Math.max(220,Math.min(650,Number(layout.cameraSideSize)||300))+"px");
    const nav=$("#camera-nav");if(nav){nav.classList.toggle("active",cameraVisible());nav.setAttribute("aria-pressed",cameraVisible()?"true":"false");}
    for(const button of $$("[data-camera-edge]"))button.classList.toggle("selected",button.dataset.cameraEdge===edge);
    if(root.CobotWorkspaceUI&&typeof root.CobotWorkspaceUI.cameraChanged==="function")root.CobotWorkspaceUI.cameraChanged();
  }
  function setCameraEdge(edge) {if(!positions.includes(edge))return;cameraResizeGuard=root.performance.now()+300;layout.cameraEdge=edge;layout.cameraOpen=true;saveLayout();applyCameraLayout();}
  function toggleCamera() {layout.cameraOpen=!layout.cameraOpen;saveLayout();applyCameraLayout();}
  function createCameraDock() {
    const main=$("main"),panel=$(".camera-panel");if(!main||!panel)return;
    const nav=element("button","nav-item camera-nav");nav.id="camera-nav";nav.type="button";
    nav.innerHTML='<span class="nav-icon">▣</span><span>相机</span>';
    nav.addEventListener("click",toggleCamera);
    const hostNav=$(".nav-item[data-view=host]");hostNav.before(nav);
    cameraDock=element("section","camera-dock");cameraDock.id="camera-dock";
    const bar=element("div","camera-dock-bar");bar.append(element("strong","","三相机"));
    const health=element("span","camera-dock-health","正在连接");health.id="camera-dock-health";health.dataset.status="unavailable";bar.append(health);
    const controls=element("div","camera-dock-controls");
    const reconnect=element("button","ghost","重连");reconnect.type="button";reconnect.title="重新连接浏览器预览流，不重启 ROS 相机节点";
    reconnect.addEventListener("click",()=>{
      for(const image of $$("#camera-dock img[data-camera]")){image.removeAttribute("src");delete image.dataset.streaming;}
      if(typeof root.CobotWorkspaceUI.cameraChanged==="function")root.CobotWorkspaceUI.cameraChanged();
      report("已重新连接画面；若仍冻结，请查看相机节点状态。","success","相机");
    });controls.append(reconnect);
    const pin=element("button","ghost","置顶");pin.type="button";pin.id="camera-pin";
    pin.addEventListener("click",()=>{layout.cameraPinned=!layout.cameraPinned;saveLayout();applyCameraLayout();});controls.append(pin);
    bar.append(controls);cameraDock.append(bar,panel);main.prepend(cameraDock);
    panel.querySelector(".camera-grid")?.replaceChildren(...["camera_left","camera_high","camera_right"].map(id=>panel.querySelector(`img[data-camera="${id}"]`)?.closest("figure")).filter(Boolean));
    bar.addEventListener("pointerdown",event=>{
      if(event.target.closest("button")||event.button!==0)return;
      const startX=event.clientX,startY=event.clientY;bar.setPointerCapture(event.pointerId);
      cameraDock.classList.add("is-moving");
      const end=up=>{
        cameraDock.classList.remove("is-moving");
        bar.removeEventListener("pointerup",end);
        if(Math.hypot(up.clientX-startX,up.clientY-startY)<20)return;
        const box=main.getBoundingClientRect();
        const distances={top:Math.abs(up.clientY-box.top),bottom:Math.abs(up.clientY-box.bottom),left:Math.abs(up.clientX-box.left),right:Math.abs(up.clientX-box.right)};
        setCameraEdge(Object.keys(distances).sort((a,b)=>distances[a]-distances[b])[0]);
      };
      bar.addEventListener("pointerup",end);
    });
    if(root.ResizeObserver)new ResizeObserver(entries=>{
      if(!cameraVisible()||root.performance.now()<cameraResizeGuard)return;
      const edge=layout.cameraEdge,rect=entries[0].contentRect,size=edge==="left"||edge==="right"?rect.width:rect.height;
      const key=edge==="left"||edge==="right"?"cameraSideSize":"cameraTopSize";
      if(size>220&&size<=650&&Math.abs(size-(layout[key]||300))>6){layout[key]=Math.round(size);saveLayout();}
    }).observe(cameraDock);
    applyCameraLayout();
  }

  function report(text,phase="success",source="操作") {
    const normalized=String(text||"").trim();if(!normalized)return;
    const fingerprint=source+"|"+phase+"|"+normalized;
    if(fingerprint===lastEvent)return;lastEvent=fingerprint;eventCount++;
    const title=$("#diag-connection"),detail=$("#diag-last-updated"),dot=$("#diag-connection-dot");
    if(title)title.textContent=phase==="error"?"失败":phase==="running"?"进行中":phase==="timeout"?"超时":"已完成";
    if(detail){detail.textContent=source+" · "+normalized;detail.title=detail.textContent;}
    if(dot){dot.classList.toggle("ok",phase==="success");dot.classList.toggle("error",phase==="error"||phase==="timeout");dot.classList.toggle("working",phase==="running");}
    const event=element("li","log-event");event.dataset.tone=phase;event.textContent=source+" · "+normalized;
    const list=$("#log-events");if(list){list.prepend(event);while(list.children.length>30)list.lastElementChild.remove();}
  }
  function renderDeviceJobs(payload) {
    jobs=payload&&payload.jobs||{};
    const select=$("#log-job-select");if(select){
      const previous=select.value||selectedJob;
      select.replaceChildren(...Object.keys(jobs).sort().map(name=>{const option=element("option","",name+" · "+jobs[name].phase);option.value=name;return option;}));
      if(previous&&jobs[previous])select.value=previous;
      else if(jobs.arms)select.value="arms";
      selectedJob=select.value;
    }
    showSelectedLog();
    const rlt=(payload.systems||{}).rlt||{};const node=$("#dashboard-rlt");
    if(node){node.dataset.tone=rlt.phase==="ready"?"green":rlt.phase==="error"?"red":"gray";node.textContent="RLT · "+(rlt.phase==="ready"?"服务在线":rlt.phase==="offline"?"未启动":"待核验");}
  }
  function showSelectedLog() {
    const text=$("#log-output"),selected=$("#log-job-select");if(!text)return;
    const job=jobs[selected&&selected.value||selectedJob];
    text.textContent=job?(job.log_tail||job.detail||"进程已登记，暂无输出。"):
      "暂无登记进程输出。选择一个进程后会显示最新日志。";
  }
  function setLogCorner(corner) {layout.logCorner=corner;saveLayout();if(logDock)logDock.dataset.corner=corner;}
  function createLogDock() {
    const indicator=$(".connection-status");if(indicator){indicator.tabIndex=0;indicator.setAttribute("role","button");indicator.title="打开进程与操作详情";
      indicator.addEventListener("click",()=>{logDock.hidden=!logDock.hidden;});
      indicator.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();indicator.click();}});
    }
    const dock=element("aside","log-dock");dock.id="log-dock";dock.hidden=true;dock.dataset.corner=layout.logCorner;
    const head=element("div","log-dock-head");head.append(element("strong","","现场输出"));
    const close=element("button","ghost panel-tool-button");close.append($("#settings-close svg").cloneNode(true));close.type="button";close.title="关闭输出窗口";close.setAttribute("aria-label",close.title);close.addEventListener("click",()=>{dock.hidden=true;});head.append(close);
    const toolbar=element("div","log-dock-toolbar");const select=element("select","");select.id="log-job-select";select.setAttribute("aria-label","选择进程输出");
    select.addEventListener("change",()=>{selectedJob=select.value;showSelectedLog();});toolbar.append(select);
    const corners=element("div","log-corner-controls");
    for(const [key,label] of [["top-left","↖"],["top-right","↗"],["bottom-left","↙"],["bottom-right","↘"]]){
      const button=element("button","ghost",label);button.type="button";button.title="吸附到"+key;button.addEventListener("click",()=>setLogCorner(key));corners.append(button);
    }
    toolbar.append(corners);
    const events=element("ol","log-events");events.id="log-events";
    const output=element("pre","log-output","暂无登记进程输出。");output.id="log-output";
    dock.append(head,toolbar,events,output);document.body.append(dock);logDock=dock;
    head.addEventListener("pointerdown",event=>{
      if(event.target.closest("button")||event.button!==0)return;
      const x=event.clientX,y=event.clientY;head.setPointerCapture(event.pointerId);
      const end=up=>{head.removeEventListener("pointerup",end);if(Math.hypot(up.clientX-x,up.clientY-y)<20)return;
        setLogCorner((up.clientY<innerHeight/2?"top":"bottom")+"-"+(up.clientX<innerWidth/2?"left":"right"));};
      head.addEventListener("pointerup",end);
    });
    $("#diag-connection").textContent="待命";$("#diag-last-updated").textContent="点击查看输出";
  }

  function animateOrder(container,firstRects) {
    for(const child of container.children){
      const before=firstRects.get(child);if(!before)continue;
      const after=child.getBoundingClientRect(),dx=before.left-after.left,dy=before.top-after.top;
      if(Math.abs(dx)+Math.abs(dy)<2)continue;
      child.animate([{transform:`translate(${dx}px,${dy}px)`},{transform:"translate(0,0)"}],{duration:250,easing:"cubic-bezier(.2,.75,.3,1)"});
    }
  }
  function enableDragGrid(container) {
    if(!container||container.dataset.dragBound)return;
    container.dataset.dragBound="true";const group=container.dataset.layoutGroup||"cards";
    const order=layout.orders[group];if(Array.isArray(order))for(const id of order){const child=[...container.children].find(node=>node.dataset.cardId===id);if(child)container.append(child);}
    container.addEventListener("dragstart",event=>{const child=event.target.closest("[data-card-id]");if(!document.body.classList.contains("editing-layout")||!child)return;
      event.dataTransfer.setData("text/plain",child.dataset.cardId);event.dataTransfer.effectAllowed="move";child.classList.add("is-dragging");});
    container.addEventListener("dragend",()=>{$$(".is-dragging").forEach(node=>node.classList.remove("is-dragging"));});
    container.addEventListener("dragover",event=>{if(document.body.classList.contains("editing-layout"))event.preventDefault();});
    container.addEventListener("drop",event=>{if(!document.body.classList.contains("editing-layout"))return;event.preventDefault();
      const id=event.dataTransfer.getData("text/plain"),source=[...container.children].find(node=>node.dataset.cardId===id),target=event.target.closest("[data-card-id]");
      if(!source||!target||source===target||target.parentElement!==container)return;
      const before=new Map([...container.children].map(node=>[node,node.getBoundingClientRect()]));
      const bounds=target.getBoundingClientRect(),singleColumn=getComputedStyle(container).gridTemplateColumns.split(/\s+/).length===1;
      const after=singleColumn?event.clientY>bounds.top+bounds.height/2:event.clientX>bounds.left+bounds.width/2;
      container.insertBefore(source,after?target.nextSibling:target);animateOrder(container,before);
      layout.orders[group]=[...container.children].map(node=>node.dataset.cardId);saveLayout();
    });
  }
  function setEditMode(enabled) {
    document.body.classList.toggle("editing-layout",enabled);
    for(const card of $$(".layout-card"))card.draggable=enabled;
    const button=$("#layout-edit");if(button)button.textContent=enabled?"完成布局":"编辑布局";
    report(enabled?"可拖动同级功能框，排序会自动保存。":"布局已保存。","success","布局");
  }
  function createSettings() {
    const settings=$("#settings-drawer");if(!settings)return;
    const section=element("section","layout-settings");section.append(element("h3","","布局"));
    const edit=element("button","secondary","编辑布局");edit.id="layout-edit";edit.type="button";
    edit.addEventListener("click",()=>setEditMode(!document.body.classList.contains("editing-layout")));
    const reset=element("button","ghost","恢复默认位置");reset.type="button";reset.addEventListener("click",()=>{layout={...initial,orders:{}};saveLayout();root.location.reload();});
    section.append(edit,reset);settings.append(section);
    for(const [group,selector] of [["rl-capture",".rl-layout"],["training","[data-page=training] .workbench-grid"]]){
      const container=$(selector);if(!container)continue;container.dataset.layoutGroup=group;
      [...container.children].forEach((node,index)=>{node.classList.add("layout-card");node.dataset.cardId=group+"-"+index;});
      enableDragGrid(container);
    }
  }
  function registerWorkspaceGrid(container,group) {
    if(!container)return;
    container.dataset.layoutGroup=group;
    for(const card of container.querySelectorAll(':scope > [data-workspace-panel]')){
      card.classList.add('layout-card');card.dataset.cardId=group+'-'+card.dataset.workspacePanel;
      card.draggable=document.body.classList.contains('editing-layout');
    }
    enableDragGrid(container);
  }
  function createCollectionSelector() {
    for(const existing of $$('.collection-switch'))existing.classList.add('hidden');
  }
  function createRltModeSelector() {
    const actions=$(".rl-process-actions");if(!actions)return;
    const modes=[["reference","Reference"],["warmup","Warmup"],["frozen","Frozen"],["online","Online"]];
    const picker=element("div","rlt-mode-picker");
    const select=element("select","");select.id="rlt-mode-choice";select.setAttribute("aria-label","运行模式");
    for(const [value,label] of modes){const option=element("option","",label);option.value=value;select.append(option);}
    const run=element("button","","启动模型");run.type="button";
    run.addEventListener("click",()=>{const button=$("#device-rlt-"+select.value);if(!button||button.disabled){report("所选模型不支持该运行模式。","error","模型");return;}button.click();});
    picker.append(select,run);actions.prepend(picker);
    for(const [value] of modes){const button=$("#device-rlt-"+value);if(button)button.classList.add("hidden");}
    const modelPicker=$(".model-compare-panel .model-picker");
    if(modelPicker){
      actions.after(modelPicker);
      const status=$("#rlt-model-status");if(status)modelPicker.append(status);
      const oldCard=$(".model-compare-panel");if(oldCard)oldCard.classList.add("hidden");
    }
    const parameters=$(".parameter-details"),training=$("[data-page=training]");
    if(parameters&&training)training.querySelector(".workbench-grid").after(parameters);
    const note=$("#rlt-model-note");if(note)note.classList.add("hidden");
  }
  function onPage(name) {
    for(const node of $$(".collection-kind"))node.value=name==="learning"?"rlt":"normal";
    if(name==="host")refreshHost();
  }
  function evaluationModelStatus() {
    const state=root.CobotDeploymentUI?.state;
    if(!state||state.phase==='offline'||!state.model)return false;
    const name=$("#dashboard-model"),actor=$("#dashboard-actor"),run=$("#dashboard-run");
    if(name)name.textContent="部署 · "+state.model.label;
    if(actor)actor.textContent=state.model.actor_version!=null?"Actor · "+state.model.actor_version:"π0.5 · step "+state.model.step;
    if(run)run.textContent=({loading:"模型加载中",ready:"模型加载成功",paused:"已暂停",running:"部署中",error:"部署异常"})[state.phase]||state.phase;
    return true;
  }
  function modelStatus(model) {
    activeModel=model;
    if(evaluationModelStatus())return;
    const name=$("#dashboard-model"),actor=$("#dashboard-actor"),run=$("#dashboard-run");if(!name)return;
    name.textContent=model?"已选 · "+model.label:"未选择模型";
    if(!actor.dataset.live)actor.textContent="Session Actor —";
    if(!run.dataset.live)run.textContent="等待现场状态";
  }
  function sessionStatus(consoleState,session) {
    if(evaluationModelStatus())return;
    const actor=$("#dashboard-actor"),run=$("#dashboard-run");if(!actor||!run)return;
    const sessionPhase=String(session&&session.phase||"");
    const phase=String((sessionPhase&&!['stopped','fault'].includes(sessionPhase)?sessionPhase:null)||consoleState&&consoleState.rlt_backend_phase||"offline");
    actor.textContent=session&&session.actor_version!=null?(sessionPhase==='stopped'?"上一 Session Actor · ":"当前 Session Actor · ")+session.actor_version:"当前 Session Actor —";
    run.textContent="后端 · "+phase;
    actor.dataset.live=run.dataset.live="true";
    const stages=$$("#model-loading span");
    const loading=phase.includes("loading_machine")?0:phase.includes("loading_env")?1:
      ["offline","stopped","fault"].includes(phase)?-1:2;
    stages.forEach((node,index)=>{node.classList.toggle("complete",index<loading);node.classList.toggle("current",index===loading);});
  }
  function facts(selector,rows) {
    const list=$(selector);if(!list)return;const signature=JSON.stringify(rows);if(list.dataset.pathFacts===signature)return;list.dataset.pathFacts=signature;list.replaceChildren();
    for(const [name,value] of rows){const key=element("dt","",name),detail=element("dd","",value==null?"—":String(value));if(typeof value==="string"&&value.startsWith("/"))root.CobotFeaturePaths?.decorateValue(detail,value);list.append(key,detail);}
  }
  function size(bytes) {return Number.isFinite(Number(bytes))?(Number(bytes)/1024**3).toFixed(1)+" GiB":"—";}
  async function refreshHost() {
    if(hostBusy||!$("[data-page=host].active"))return;hostBusy=true;
    const message=$("#host-message");
    try{
      const responses=await Promise.all([fetch("/api/console/host"),fetch("/api/console/cameras")]);
      const host=responses[0].ok?await responses[0].json():null;
      const camera=responses[1].ok?await responses[1].json():{};
      if(!host)throw new Error("本机信息 HTTP "+responses[0].status);
      const disk=host.disk||{},paths=host.paths||{},scripts=host.scripts||{};
      facts("#host-runtime",[["主机",host.hostname],["网页进程",host.console_pid],["CPU 核数",host.cpu_count],["1 分钟负载",host.load_1m]]);
      const control=(activeModel&&activeModel.parameters||[]).find(item=>item.group==='Runtime'&&['logical control / Replay rate','control rate'].includes(item.key));
      const collection=root.CobotCollectionModel?.state;
      const runtime=collection&&collection.phase!=='offline'?collection:root.CobotDeploymentUI?.state;
      const execution=runtime?.session?.execution||runtime?.model?.execution_settings;
      const publish=runtime?.phase!=='offline'&&!runtime?.status_stale?(execution?.publish_hz??runtime?.model?.publish_hz??runtime?.model?.control_hz):null;
      facts("#host-frequencies",[["动作发布频率",publish==null?"模型未加载或配置未核验":publish+" Hz"],["逻辑步频 / Replay",execution?.logical_hz?execution.logical_hz+" Hz":control?control.value+" "+(control.unit||""):"当前模型未登记"],["三相机预览",camera.preview_fps==null?"—":camera.preview_fps+" FPS"],["预览上限",host.camera_preview_limit_fps+" FPS"],["相机同步",camera.status||"未核验"],["相机间偏差",camera.skew_ms==null?"—":Number(camera.skew_ms).toFixed(1)+" ms"]]);
      facts("#host-paths",Object.entries(paths));facts("#host-scripts",Object.entries(scripts));
      const used=$("#host-disk-used"),label=$("#host-disk-label");if(used)used.style.width=Math.min(100,100*Number(disk.used_bytes||0)/Math.max(1,Number(disk.total_bytes||0)))+"%";
      if(label)label.textContent=`空余 ${size(disk.free_bytes)} / 总计 ${size(disk.total_bytes)} · ${disk.path||""}`;
      if(message)message.textContent="本机信息已更新";
    }catch(error){if(message)message.textContent="本机信息不可用："+error.message;}
    finally{hostBusy=false;}
  }
  function mount() {
    if(mounted)return;mounted=true;layout=readLayout();
    createCameraDock();buildSystem();createLogDock();createCollectionSelector();createRltModeSelector();createSettings();
    registerWorkspaceGrid($("[data-page=deployment] .session-workspace"),"deployment-workspace");
    const captureGrid=$("[data-page=operation] .capture-layout"),historyMount=$("#episode-browser-operation");
    if(captureGrid&&historyMount)captureGrid.append(historyMount);
    const rlGrid=$("[data-page=learning] .rl-layout"),rlHistory=$("#episode-browser-learning");
    if(rlGrid&&rlHistory){rlGrid.prepend(rlHistory);const storage=rlGrid.querySelector(".rlt-only:not(.action-panel):not(.rl-mode-card)");if(storage)rlGrid.prepend(storage);}
    root.CobotCollectionModel?.mount();
    const training=$("[data-page=training]"),figures=$("#training-figures");
    const comparison=$("[data-page=learning] .model-compare-panel"),process=$("[data-page=learning] .rl-process-panel");
    if(comparison&&process){const picker=comparison.querySelector(".model-picker");if(picker)process.append(picker);if(training)training.insertBefore(comparison,figures||null);}
    for(const selector of ["[data-page=learning] .learning-grid","[data-page=learning] .chart-grid"]){
      const node=$(selector);if(node&&training){node.classList.remove("rlt-only","hidden");training.insertBefore(node,figures||null);}
    }
    const sidebar=$(".sidebar"),brand=$(".brand");
    if(sidebar&&brand){
      const toggle=element("button","sidebar-toggle","‹");toggle.type="button";toggle.title="收起侧栏";toggle.setAttribute("aria-label",toggle.title);toggle.setAttribute("aria-expanded","true");
      toggle.addEventListener("click",()=>{sidebar.classList.toggle("is-collapsed");const closed=sidebar.classList.contains("is-collapsed");document.body.classList.toggle("sidebar-is-collapsed",closed);toggle.textContent=closed?"›":"‹";if(!matchMedia("(prefers-reduced-motion: reduce)").matches){for(const icon of sidebar.querySelectorAll(".nav-icon,.brand-mark,.settings-button")){icon.getAnimations().forEach(a=>a.cancel());icon.animate([{opacity:.45,transform:"scale(.84)"},{opacity:1,transform:"scale(1)"}],{duration:240,easing:"cubic-bezier(.2,.7,.2,1)"});}}toggle.title=closed?"展开侧栏":"收起侧栏";toggle.setAttribute("aria-label",toggle.title);toggle.setAttribute("aria-expanded",String(!closed));});
      brand.after(toggle);
    }
    root.setInterval(refreshHost,5000);
    root.CobotUnifiedCollection?.mount();
  }
  const api={mount,registerWorkspaceGrid,report,renderDeviceJobs,modelStatus,sessionStatus,onPage,cameraVisible,cameraChanged:null};
  root.CobotWorkspaceUI=api;
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  if(typeof document!=="undefined"){
    if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",mount);
    else mount();
  }
})(typeof globalThis!=="undefined"?globalThis:this);
