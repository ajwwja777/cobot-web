"use strict";
(() => {
 const $=id=>document.getElementById(id),e=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
 const t=(zh,en)=>window.CobotPreferences?.language==="en"?en:zh;
 let state={},selected="",root="",pending=false,records=[],uncertain=true,notice="",polling=false,recordsReady=false;
 let values={};try{values=JSON.parse(localStorage.getItem("cobot-motion-options")||"{}");}catch(_){}
 const videoRoot=values.video_root||"/media/agilex/Getea1/jiaan/data/videos/deployment";
 function label(title,input){const node=e("label"),span=e("span",title);if(input.type==="checkbox"){node.className="checkbox-row";node.append(input,span);}else node.append(span,input);return node;}
 function button(id,title,action){const node=e("button",title);node.id=id;node.type="button";node.addEventListener("click",()=>run(action));return node;}
 function panel(title,id){const node=e("article");node.className="panel motion-panel";node.id=id;node.append(e("h3",title));return node;}
 const record=panel(t("轨迹录制","Record trajectory"),"motion-record-panel"),replay=panel(t("轨迹回放","Replay trajectory"),"motion-replay-panel");
 replay.dataset.workspacePanel="trajectory";
 const name=e("input");name.id="motion-name";name.maxLength=120;name.placeholder=t("轨迹名称","Trajectory name");
 const recordRoot=e("input"),replayRoot=e("input");recordRoot.id="motion-record-root";replayRoot.id="motion-replay-root";
 const select=e("select");select.id="motion-trace";select.setAttribute("aria-label",t("已保存轨迹","Saved trajectory"));
 const rename=e("input");rename.id="motion-rename";rename.maxLength=120;
 const recordState=e("p"),replayState=e("p"),log=e("pre");recordState.id="motion-record-status";replayState.id="motion-replay-status";log.id="motion-log";
 for(const node of [recordState,replayState]){node.className="inline-status";node.setAttribute("role","status");}
 const rc=e("div"),rp=e("div");rc.className=rp.className="button-row";
 rc.append(button("motion-record",t("开始录制轨迹","Record trajectory"),"record"),button("motion-record-stop",t("结束并保存","Finish and save"),"stop"));
 rp.append(button("motion-replay",t("开始回放","Start replay"),"replay"),button("motion-replay-stop",t("停止回放","Stop replay"),"stop"));
 const refresh=button("motion-refresh",t("刷新轨迹","Refresh trajectories"),"refresh"),renameButton=button("motion-save-name",t("修改名称","Rename"),"rename");
 const videoToggle=e("input");videoToggle.id="motion-keep-video";videoToggle.type="checkbox";
 const videoInput=e("input");videoInput.id="motion-video-root";videoInput.value=videoRoot;
 const replayVideoOptions=e("div");replayVideoOptions.hidden=true;replayVideoOptions.append(label(t("视频保存目录","Video directory"),videoInput));
 videoToggle.addEventListener("change",()=>replayVideoOptions.hidden=!videoToggle.checked);
 record.append(label(t("名称","Name"),name),label(t("轨迹目录","Trajectory directory"),recordRoot),rc,recordState);
 const chooseRow=e("div"),renameRow=e("div");chooseRow.className=renameRow.className="motion-inline-row";
 chooseRow.append(select,refresh);renameRow.append(label(t("轨迹名称","Trajectory name"),rename),renameButton);
 replay.append(label(t("轨迹目录","Trajectory directory"),replayRoot),chooseRow,renameRow,label(t("保留三路原视频","Keep three original videos"),videoToggle),replayVideoOptions,rp,e("small",t("回放会缓慢回到录制起点；示教接管后停止。","Replay returns slowly to the start; teaching takeover stops playback.")),replayState,log);
 function mountMode(pageName,gridSelector,panel,modelTitle,motionTitle){
  const page=document.querySelector('[data-page="'+pageName+'"]'),grid=page?.querySelector(gridSelector);
  if(!page||!grid)return null;
  const tabs=e("div");tabs.className="motion-mode-tabs";tabs.id="motion-"+pageName+"-tabs";tabs.setAttribute("role","tablist");tabs.setAttribute("aria-label",t("工作模式","Workspace mode"));
  const normal=e("button",modelTitle),trajectory=e("button",motionTitle);
  normal.type=trajectory.type="button";normal.id="motion-"+pageName+"-normal";trajectory.id="motion-"+pageName+"-trajectory";
  const view=e("section");view.id="motion-"+pageName+"-workspace";view.className="motion-mode-workspace";view.append(panel);
  if(!grid.id)grid.id="motion-"+pageName+"-existing";
  for(const [button,target] of [[normal,grid],[trajectory,view]]){button.setAttribute("role","tab");button.setAttribute("aria-controls",target.id);}
  tabs.append(normal,trajectory);grid.before(tabs,view);
  let current="normal";
  function select(mode){current=mode;const active=mode==="trajectory";
   grid.classList.toggle("motion-mode-hidden",active);view.classList.toggle("motion-mode-hidden",!active);
   normal.setAttribute("aria-selected",String(!active));trajectory.setAttribute("aria-selected",String(active));
   normal.classList.toggle("selected",!active);trajectory.classList.toggle("selected",active);
  }
  normal.addEventListener("click",()=>select("normal"));trajectory.addEventListener("click",()=>select("trajectory"));
  select("normal");return {get mode(){return current;},select};
 }
 const recordMode=mountMode("operation",".capture-layout",record,t("数据采集","Episode collection"),t("轨迹录制","Record trajectory"));
 const replayMode=mountMode("deployment",".session-workspace",replay,t("模型部署","Model deployment"),t("轨迹回放","Replay trajectory"));
 // Hidden model/episode controls must not receive global robot shortcuts.
 document.addEventListener("keydown",event=>{
  const active=document.querySelector('[data-page].active')?.dataset.page;
  if(!((active==="operation"&&recordMode?.mode==="trajectory")||(active==="deployment"&&replayMode?.mode==="trajectory")))return;
  if(event.target.closest("input,select,textarea,[contenteditable=true]"))return;
  if(["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"," ","Spacebar"].includes(event.key)){
   event.stopImmediatePropagation();if(!event.target.closest("button"))event.preventDefault();
  }
 },true);

 function save(){localStorage.setItem("cobot-motion-options",JSON.stringify({data_root:root,video_root:$("deploy-video-root")?.value||videoInput.value}));}
 for(const input of [recordRoot,replayRoot])input.addEventListener("change",()=>{recordsReady=false;root=input.value.trim();recordRoot.value=replayRoot.value=root;save();loadRecords().catch(showError);});
 select.addEventListener("change",()=>{selected=select.value;rename.value=records.find(r=>r.id===selected)?.name||"";render();});
 const deployToggle=$("deploy-keep-video"),deployOptions=$("deploy-video-options"),deployRoot=$("deploy-video-root");
 deployRoot.value=videoRoot;deployToggle.addEventListener("change",()=>deployOptions.hidden=!deployToggle.checked);deployRoot.addEventListener("change",save);videoInput.addEventListener("change",()=>{deployRoot.value=videoInput.value;save();});
 const picker=window.CobotPathPicker?.create?.({input:deployRoot,panel:$("deploy-video-browser"),list:$("deploy-video-paths"),hint:$("deploy-video-hint"),fetchDirectories:path=>request("/api/segmented-teach/storage/directories?path="+encodeURIComponent(path)),storage:localStorage,storageKey:"cobot-recent-video-paths"});
 $("deploy-video-browse").addEventListener("click",()=>picker?.refresh());
 async function request(path,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const response=await fetch(path,{cache:"no-store",signal:controller.signal,...(body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==="string"?result.detail:JSON.stringify(result.detail||result.error));return result;}catch(error){if(error.name==="AbortError"||error instanceof TypeError){const failure=Error(t("连接超时或中断，操作结果待确认；请先查看任务状态。","Connection interrupted; the operation result is unconfirmed. Check task status first."));failure.transport=true;throw failure;}throw error;}finally{clearTimeout(timer);}}

 function showError(error){notice=error.message;recordState.textContent=replayState.textContent=notice;window.CobotWorkspaceUI?.report(error.message,"error",t("轨迹","Trajectory"));}
 function videoText(video){if(!video)return "";return (video.state==="error"?t("录像异常：","Video error: ")+video.error:video.state==="saved"?t("原视频已保存：","Original videos saved: "):t("正在录制原视频：","Recording original videos: "))+video.directory;}
 function render(){const busy=pending||state.active,valid=records.find(r=>r.id===selected)?.valid;
  $("motion-record").disabled=busy||uncertain;$("motion-replay").disabled=busy||uncertain||!valid;
  $("motion-record-stop").disabled=pending||!state.active||state.action!=="record";$("motion-replay-stop").disabled=pending||!state.active||state.action!=="replay";
  for(const input of [recordRoot,replayRoot,name,select,rename,renameButton,videoToggle,videoInput])input.disabled=busy;
  const phases={idle:t("待开始","Idle"),starting:t("准备中","Preparing"),recording:t("录制命令运行中","Record command running"),replaying:t("回放命令运行中","Replay command running"),stopping:t("收尾中","Finishing"),completed:t("已完成","Complete"),interrupted:t("已中断，查看输出","Interrupted; check output"),error:t("异常，查看输出","Error; check output")};
  const text=(phases[state.phase]||state.phase||"")+(state.video?" · "+videoText(state.video):"");recordState.textContent=replayState.textContent=notice||text;log.textContent=state.log_tail||"";
 }
 async function loadRecords(){const data=await request("/api/motion/records?data_root="+encodeURIComponent(root));records=data.records;recordsReady=true;select.replaceChildren(...records.map(r=>{const option=e("option",r.name+" · "+r.frames+t(" 帧"," frames")+(r.valid?"":t(" · 不完整"," · incomplete")));option.value=r.id;return option;}));if(!records.length)select.append(e("option",t("暂无轨迹","No trajectories")));if(records.some(r=>r.id===selected))select.value=selected;else selected=select.value;rename.value=records.find(r=>r.id===selected)?.name||"";render();}
 async function run(action){if(pending)return;notice="";pending=true;render();try{
  if(action==="refresh"){await loadRecords();return;}
  const body={action};
  if(action==="stop")body.run_id=state.id;
  else {body.data_root=root;body.trace_id=selected;if(action==="rename")body.name=rename.value.trim();if(action==="record")body.name=name.value.trim();if(["record","replay"].includes(action))body.confirmed=true;if(action==="replay"&&videoToggle.checked)body.video={enabled:true,data_root:videoInput.value.trim()};}
  const data=await request("/api/motion/action",body);if(action==="rename")await loadRecords();else state=data;
 }catch(error){if(error.transport)uncertain=true;pending=false;render();showError(error);return;}finally{pending=false;}render();}
 async function poll(){if(polling)return;polling=true;try{const before=state.active,previous=state.id;state=await request("/api/motion/status");if(state.active||previous!==state.id)notice="";uncertain=false;if(!root){root=values.data_root||state.default_data_root;recordRoot.value=replayRoot.value=root;}if(!recordsReady||(before&&!state.active))await loadRecords();render();const deployment=window.CobotDeploymentUI?.state;$("deploy-video-status").textContent=videoText(deployment?.video);const locked=Boolean(deployment?.active||deployment?.operation);deployToggle.disabled=deployRoot.disabled=locked;$("deploy-video-browse").disabled=locked;}catch(error){uncertain=true;render();showError(error);}finally{polling=false;}}
 window.CobotMotionUI={recordMode,replayMode,videoOptions:()=>deployToggle.checked?{enabled:true,data_root:deployRoot.value.trim()}:null,poll};
 poll();setInterval(()=>{if(!document.hidden)poll();},1200);
})();
