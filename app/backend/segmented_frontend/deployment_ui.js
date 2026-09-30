"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, className) => { const n=document.createElement(tag); if(text!=null)n.textContent=text; if(className)n.className=className; return n; };
  const phaseName = {process_running:"进程已启动（模型未验证）",checking:"核对中",offline:"未加载",loading:"加载中",ready:"加载成功",paused:"已暂停",running:"部署中",error:"异常"};
  const operationName={load:"加载模型",unload:"释放模型",start:"开始部署",pause:"暂停",resume:"继续",success:"记录成功",failure:"记录失败",abort:"放弃本轮"};
  const outcomes={success:"成功",failure:"失败",abort:"放弃",start_failed:"启动失败"};
  let state=null, localBusy=false, selectedModel="", modelSignature="", selectedRecord="", records=[], recordKey="", lastNotice="", lastLoaded="", activeBefore=null,loadedSelectionKey="";
  const selectableArms=["front-left","front-right","mid","rear-left","rear-right"];
  const homeArms=()=>[...document.querySelectorAll("#deployment-home-arms input:checked")].map(input=>input.value);
  let homePoses={}, outputsBusy=false, statusBusy=false, recordRequest=0;
  let connectionError="",transportMessage=false,storageInitialized=false,recentDirectories=null,directoryPicker=null;
  const modelPicker=window.CobotModelPicker.create({container:$("deployment-model-picker"),modelSelect:$("deployment-model"),sceneId:"deployment-scene",onChange:selection=>{selectedModel=selection.modelId;selectedRecord="";describeModel();renderControls();if(selection.source==="user"&&selection.model?.data_directories?.evaluation)applyModelDirectory(selection.model.data_directories.evaluation);else refreshRecords();}});
  const executionHost=el("section");$("deployment-model-picker").after(executionHost);
  const executionOptions=window.CobotExecutionOptions?.create(executionHost,{onChange:describeModel});
  const runtimeHelp=el("section");$("deployment-load-state").after(runtimeHelp);
  const runtimeRecovery=window.CobotRuntimeRecovery?.create(runtimeHelp,{refresh:()=>poll()});
  const sleep = ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function notify(text,error=false,tone=null){transportMessage=false;$("deployment-message").textContent=text;$("deployment-message").classList.toggle("error",error);window.CobotWorkspaceUI?.report(text,tone||(error?"error":"success"),"部署");}
  async function applyModelDirectory(path){
    if(localBusy||state?.active||state?.operation)return;
    localBusy=true;storageInitialized=true;$("deployment-storage").value=path;renderControls();
    try{const result=await request("/api/deployment/storage",{data_root:path});state.data_root=result.data_root;recentDirectories?.remember(result.data_root);delete $("deployment-storage").dataset.dirty;selectedRecord="";await refreshRecords();notify("保存位置已更新："+result.data_root);}
    catch(error){$("deployment-storage").dataset.dirty="true";notify(error.message,true);}
    finally{localBusy=false;renderControls();}
  }
  async function request(path, body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);try{const r=await fetch(path,{cache:"no-store",signal:controller.signal,...(body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});let p;try{p=await r.json();}catch(error){if(controller.signal.aborted)throw error;throw new Error("服务返回异常（HTTP "+r.status+"）");}if(!r.ok)throw new Error(typeof p.detail==="string"?p.detail:JSON.stringify(p.detail||p.error||r.status));return p;}catch(error){if(controller.signal.aborted||error.name==="AbortError"||error instanceof TypeError){const failure=new Error(controller.signal.aborted||error.name==="AbortError"?"请求超时（12 秒），正在重新连接":"连接中断，正在重新连接");failure.transport=true;throw failure;}throw error;}finally{clearTimeout(timer);}}
  function model(){return state?.models?.find(m=>m.id===selectedModel);}
  function describeModel(){const m=model(),facts=$("deployment-facts");if(!m){window.CobotModelPicker.renderDetails(facts,null);renderControls();return;}
    executionOptions?.update(m,state,localBusy||Boolean(state.operation)||Boolean(state.active));
    window.CobotModelPicker.renderDetails(facts,executionOptions?.describe(state.model?.id===m.id&&state.phase!=='offline'?state.model:m)||m);
    window.CobotFeaturePaths?.update("deployment",{model:m,state});
    $("deploy-home-pose").dataset.preferred=m.home_pose;renderHomePoses();renderControls();
  }
  function renderHomePoses(){const select=$("deploy-home-pose"),target=$("deploy-home-target").value;const previous=select.value,arms=homeArms(),values=target==="selection"?(arms.length?arms.reduce((shared,arm)=>shared.filter(pose=>(homePoses[arm]||[]).includes(pose)),homePoses[arms[0]]||[]):[]):homePoses[target]||[];
    $("deployment-home-arms").hidden=target!=="selection";const preferred=select.dataset.preferred;select.replaceChildren(...values.map(v=>{const o=el("option",v);o.value=v;return o;}));select.value=values.includes(preferred)?preferred:values.includes(previous)?previous:(values[0]||"");if(values.length)delete select.dataset.preferred;}
  function renderControls(){if(!state)return;const uncertain=Boolean(connectionError||state.status_stale),busy=localBusy||Boolean(state.operation),loaded=["ready","paused","running"].includes(state.phase),active=Boolean(state.active),same=state.model?.id===selectedModel;
    $("deploy-load").disabled=busy||uncertain||(state.phase!=="offline"&&!(state.phase==="error"&&state.runtime_failure?.recoverable&&model()?.kind==="rlt"))||!window.CobotModelPicker.available(model());
    $("deploy-unload").disabled=busy||state.phase==="offline";
    $("deploy-start").disabled=busy||uncertain||active||!loaded||!same||state.phase==="running"||state.model?.evaluation_allowed===false||model()?.capabilities?.start===false;
    $("deploy-pause").disabled=busy||!active||state.phase!=="running"||model()?.capabilities?.pause===false;
    $("deploy-resume").disabled=busy||uncertain||!active||state.phase!=="paused"||model()?.capabilities?.resume===false;
    for(const name of ["success","failure"]){$("deploy-"+name).disabled=busy||uncertain||!active||!loaded;}
    $("deploy-abort").disabled=busy||!active;
    $("deployment-storage-apply").disabled=active;
    recentDirectories?.setDisabled(active);recentDirectories?.update();
    $("deployment-storage").disabled=false;
    $("deployment-recording-directory").textContent=state.data_root||"—";
    $("deploy-home").disabled=busy||uncertain||!$("deploy-home-pose").value;
    modelPicker.setDisabled(busy||active||state.phase==="loading");
    executionOptions?.update(model(),state,busy||active||state.phase==='loading'||Boolean(state.session&&!['disarmed','ready','waiting_scene','stopped'].includes(state.session.phase)));
    $("deployment-gate").textContent=uncertain?"状态待确认":phaseName[state.phase]||state.phase;
    $("deployment-gate").dataset.tone=uncertain?"amber":state.phase==="error"?"red":loaded?"green":state.phase==="loading"?"amber":"gray";
    $("deployment-trial-state").textContent=uncertain?"重新连接中":active?(state.active.intervened?"人工介入 · ":"")+(phaseName[state.phase]||state.phase):loaded?(state.model?.evaluation_allowed===false?"在线更新模型 · 请在采集页开始 Session":"待开始"):"等待加载";
    const detail=state.error||state.detail;
    const loading=state.phase==="loading"||state.operation==="load";
    const text=uncertain?(connectionError||"状态更新延迟，正在核对模型状态"):state.error||(loading?window.CobotModelLoading(state.model||model()).zh:loaded?"模型加载成功："+(state.model?.label||""):detail||(state.phase==="offline"?"未加载":state.phase==="error"?"模型运行异常，请查看输出":"正在核对模型状态"));
    $("deployment-load-state").textContent=state.runtime_failure?window.CobotRuntimeRecovery.summary(state):text;
    runtimeRecovery?.update(state);
    $("deployment-load-state").classList.toggle("error",!uncertain&&(state.phase==="error"||Boolean(state.error)));
    $("deploy-load").classList.remove("is-loading");
  }
  function renderStatus(next){const loadingChanged=!next.status_stale&&next.phase==="loading"&&(state?.phase!=="loading"||state?.model?.id!==next.model?.id||state?.started_at!==next.started_at);connectionError="";state=next;if(loadingChanged)notify(window.CobotModelLoading(next.model||model()).zh,false,"running");if(transportMessage&&!next.status_stale){notify(next.operation?operationName[next.operation]+"中":"连接已恢复");}const signature=JSON.stringify([window.CobotPreferences?.language,next.models]);
    if(Array.isArray(next.models)){
      const current=selectedModel||localStorage.getItem("cobot-capture-model")||next.selected_model;
      selectedModel=modelPicker.update(next.models,current);
      if(signature!==modelSignature){modelSignature=signature;describeModel();}
    }
    const loadedKey=next.model?.id+":"+next.started_at;
    if(next.model&&next.phase!=="offline"&&loadedKey!==loadedSelectionKey){
      loadedSelectionKey=loadedKey;selectedModel=modelPicker.select(next.model.id,{loaded:true});describeModel();
    }
    if(document.activeElement!==$("deployment-storage")&&!$("deployment-storage").dataset.dirty)$("deployment-storage").value=next.data_root;
    if(!next.status_stale&&["ready","paused","running"].includes(next.phase)&&next.model){const key=next.model.id+":"+next.started_at;if(lastLoaded!==key){lastLoaded=key;notify("模型加载成功："+next.model.label);}}
    if(next.error&&next.error!==lastNotice){lastNotice=next.error;notify(next.error,true);}if(!next.error)lastNotice="";
    if(next.active?.id&&next.active.id!==activeBefore)selectedRecord=next.active.id;
    if(activeBefore&&!next.active)selectedRecord=activeBefore;
    const key=[selectedModel,next.data_root,next.active?.id||"",next.active?.intervened||false].join("|");
    if(key!==recordKey){recordKey=key;refreshRecords();}
    if(activeBefore&&!next.active)refreshRecords();activeBefore=next.active?.id||null;
    renderControls();
    window.CobotDeploymentUI.state=next;
    window.CobotFeaturePaths?.update("deployment",{model:model(),state:next});
    if(next.phase!=="offline"&&next.model)window.CobotWorkspaceUI?.sessionStatus(null,null);
  }
  async function poll(){if(statusBusy)return;statusBusy=true;try{
    let next=await request("/api/deployment/status");
    if(!storageInitialized&&!next.status_stale&&!next.operation){
      storageInitialized=true;
      recentDirectories?.remember(next.data_root);
      if(next.default_data_root&&!next.read_only&&!next.active&&!next.operation){
        try{const result=await request("/api/deployment/storage",{data_root:next.default_data_root,reset_on_refresh:true});next={...next,data_root:result.data_root};}
        catch(error){notify((window.CobotPreferences?.language==="en"?"Test directory was not selected: ":"测试目录未切换：")+error.message,true);}
      }
    }
    renderStatus(next);
  }catch(error){connectionError=error.message;if(state)renderControls();else $("deployment-load-state").textContent=connectionError;}finally{statusBusy=false;}}
  async function act(action){if(localBusy)return false;localBusy=true;renderControls();notify(action==="load"?window.CobotModelLoading(model()).zh:operationName[action]+"中",false,"running");try{let next=await request("/api/deployment/action",{action,model_id:selectedModel,trial_id:state?.active?.id||null,...(action==="load"&&executionOptions?.value?{execution_options:executionOptions.value}:{})});renderStatus(next);window.CobotOutputPanel?.follow({id:'deployment',component:'deployment'});for(let i=0;next.operation&&i<150;i++){await sleep(600);next=await request("/api/deployment/status");renderStatus(next);}if(next.operation)throw new Error("操作仍在进行，请到输出页查看进度");if(next.error)throw new Error(next.error);if(action!=="load")notify(operationName[action]+"完成");else if(!["ready","paused","running"].includes(next.phase))notify(window.CobotModelLoading(next.model||model()).zh);await refreshRecords();return true;}catch(error){if(error.transport){connectionError=error.message;notify(error.message+"；操作结果待确认，请勿重复点击。后台操作可能仍在执行。",true);transportMessage=true;}else notify(error.message,true);return false;}finally{localBusy=false;renderControls();}}
  async function home(confirmed=false){const pose=$("deploy-home-pose").value;if(!pose)return;if(state?.active){if(!await act("abort"))return;}return window.CobotDeviceUI?.execute({component:"home",action:"run",target:$("deploy-home-target").value,...($("deploy-home-target").value==="selection"?{arms:homeArms()}:{}),pose},"归位到 "+pose,confirmed);}
  async function terminal(action){if(await act(action)){if($("deploy-auto-home").checked)await home(true);}}
  async function refreshRecords(){if(!state)return;const token=++recordRequest;const key=selectedModel;try{const result=await request("/api/deployment/records?model_id="+encodeURIComponent(key));if(token!==recordRequest)return;records=result.records;$("deploy-total").textContent=result.total;$("deploy-success-count").textContent=result.success;$("deploy-failure-count").textContent=result.failure;$("deploy-rate").textContent=result.rate==null?"—":(result.rate*100).toFixed(1)+"%";$("deploy-autonomous").textContent="自主成功率 "+(result.autonomous_rate==null?"—":(result.autonomous_rate*100).toFixed(1)+"%");$("deploy-aborted").textContent="放弃 "+result.aborted;$("deployment-result-scope").textContent="当前模型";
      const select=$("deployment-records");select.replaceChildren(...records.map(r=>{const o=el("option",new Date(r.started_at*1000).toLocaleString()+" · "+(outcomes[r.outcome]||"进行中")+(r.intervened?" · 人工介入":""));o.value=r.id;return o;}));if(!records.length){const o=el("option","暂无记录");select.append(o);}if(records.some(r=>r.id===selectedRecord))select.value=selectedRecord;else selectedRecord=records[0]?.id||"";renderFrames();
    }catch(error){if(token===recordRequest)notify("评估记录读取失败："+error.message,true);}}
  function renderFrames(){const record=records.find(r=>r.id===selectedRecord),container=$("deployment-frames");window.CobotFeaturePaths?.update("deployment-record",{record});container.replaceChildren();for(const [key,label] of [["start","首帧"],["end","末帧"]]){const section=el("section",null,"deployment-frame-block");section.append(el("h4",label));const images=el("div",null,"deployment-frame-triplet");if(record?.[key]?.urls?.length){for(const [i,url] of record[key].urls.entries()){const figure=el("figure"),img=el("img"),caption=el("figcaption",["左腕","顶部","右腕"][i]);img.src=url;img.alt=label+caption.textContent;img.decoding="async";img.addEventListener("error",()=>{caption.textContent+=" · 未加载";});figure.append(img,caption);images.append(figure);}}else{images.append(el("div",record?.[key]?.error|| (record?"等待记录":"暂无画面"),"deployment-frame-empty"));}section.append(images);container.append(section);}}
  const taskNames={can:"CAN",roscore:"ROS",arms:"机械臂",cameras:"相机",home:"归位",pose:"位置",recover:"恢复",rlt:"RLT",rlt_model:"模型选择",deployment:"部署",stage1:"Stage 1 模型",pi05:"π0.5 模型"};
  const taskPhases={...phaseName,running:"运行中",completed:"已完成",failed:"失败",stopping:"停止中",stopped:"已停止",stale:"进程已退出",archived:"历史",previous:"最近输出",model:"模型日志",invalid:"无任务"};
  async function refreshOutputs(){if(window.CobotOutputPanel)return window.CobotOutputPanel.refresh();if(outputsBusy)return;outputsBusy=true;try{const result=await request("/api/console/outputs?history="+$("outputs-history").checked),filter=$("outputs-filter"),previous=filter.value;const components=[...new Set(result.tasks.map(j=>j.component))];if(JSON.stringify(components)!==filter.dataset.signature){filter.dataset.signature=JSON.stringify(components);filter.replaceChildren();for(const key of ["all",...components]){const option=el("option",key==="all"?"全部任务":taskNames[key]||key);option.value=key;filter.append(option);}filter.value=components.includes(previous)?previous:"all";}
      const grid=$("outputs-grid"),visible=result.tasks.filter(j=>(filter.value==="all"||j.component===filter.value)&&($("outputs-history").checked||j.phase!=="archived")),ids=new Set(visible.map(j=>String(j.id)));
      for(const child of [...grid.children])if(!ids.has(child.dataset.job))child.remove();
      for(const job of visible){let card=[...grid.children].find(c=>c.dataset.job===String(job.id));if(!card){card=el("article",null,"panel output-card");card.dataset.job=String(job.id);const header=el("div",null,"panel-head"),title=el("h3",taskNames[job.component]||job.component),phase=el("span",null,"state-badge"),path=el("div",null,"output-path-container"),pre=el("pre");pre.tabIndex=0;header.append(title,phase);card.append(header,path,pre);grid.append(card);}const badge=card.querySelector(".state-badge");badge.textContent=taskPhases[job.phase]||job.phase;badge.dataset.tone=["failed","error"].includes(job.phase)?"red":["running","ready","completed"].includes(job.phase)?"green":"gray";const pathNode=card.querySelector(".output-path-container");if(pathNode.dataset.path!==String(job.log_path||"")){pathNode.dataset.path=job.log_path||"";pathNode.textContent=job.log_path||"";window.CobotFeaturePaths?.decorateValue(pathNode,job.log_path);}const pre=card.querySelector("pre"),text=job.log_tail||job.detail||"暂无输出";if(pre.textContent!==text){const follow=pre.scrollHeight-pre.scrollTop-pre.clientHeight<40,top=pre.scrollTop;pre.textContent=text;pre.scrollTop=follow?pre.scrollHeight:top;}}
      if(!visible.length)grid.append(el("p","暂无任务输出","outputs-empty"));$("outputs-status").textContent=new Date(result.updated_at*1000).toLocaleTimeString();
    }catch(error){$("outputs-status").textContent=error.message;}finally{outputsBusy=false;}}
  for(const action of ["load","unload","start","pause","resume"])$("deploy-"+action).addEventListener("click",()=>act(action));
  for(const action of ["success","failure","abort"])$("deploy-"+action).addEventListener("click",()=>terminal(action));
  $("deployment-storage-apply").addEventListener("click",async()=>{storageInitialized=true;try{const result=await request("/api/deployment/storage",{data_root:$("deployment-storage").value.trim()});state.data_root=result.data_root;recentDirectories?.remember(result.data_root);delete $("deployment-storage").dataset.dirty;selectedRecord="";await refreshRecords();notify("保存位置已更新");}catch(error){notify(error.message,true);}});
  $("deployment-storage").addEventListener("input",()=>{$("deployment-storage").dataset.dirty="true";});
  $("deployment-records").addEventListener("change",()=>{selectedRecord=$("deployment-records").value;renderFrames();});
  for(const [index,arm] of selectableArms.entries()){
    const label=el("label"),input=el("input"),caption=el("span");
    input.type="checkbox";input.value=arm;input.checked=arm!=="mid";
    caption.dataset.zh=["左前臂","右前臂","中臂","左后臂","右后臂"][index];
    caption.dataset.en=["Front left","Front right","Middle","Rear left","Rear right"][index];
    caption.textContent=window.CobotPreferences?.language==="en"?caption.dataset.en:caption.dataset.zh;
    label.append(input,caption);$("deployment-home-arms").append(label);
    input.addEventListener("change",()=>{renderHomePoses();renderControls();});
  }
  $("deploy-home-target").addEventListener("change",()=>{renderHomePoses();renderControls();});$("deploy-home").addEventListener("click",()=>home());
  for(const id of ["outputs-filter","outputs-history"])$(id).addEventListener("change",refreshOutputs);$("outputs-refresh").addEventListener("click",refreshOutputs);
  document.addEventListener("keydown",event=>{if(!document.querySelector('[data-page="deployment"].active')||event.repeat||event.ctrlKey||event.metaKey||event.altKey||event.target.closest("input,select,textarea,[contenteditable=true]")||document.querySelector("dialog[open]"))return;const action=event.key===" "||event.key==="Spacebar"?(state?.active?(state.phase==="running"?"pause":"resume"):null):event.key==="ArrowRight"?state?.active?(state.phase==="running"?"pause":"resume"):"start":({ArrowUp:"success",ArrowDown:"failure",ArrowLeft:"abort"}[event.key]);if(!action)return;event.preventDefault();const button=$("deploy-"+action);if(button&&!button.disabled)button.click();});
  window.addEventListener("cobot:model-default",event=>{selectedModel=modelPicker.select(event.detail);modelSignature="";poll();});
  window.CobotDeploymentUI={get state(){return state;},set state(value){state=value;},poll,refreshOutputs};
  document.querySelector('[data-view="outputs"]').addEventListener("click",refreshOutputs);document.querySelector('[data-view="deployment"]').addEventListener("click",()=>{poll();refreshRecords();});
  async function devices(){try{const d=await request("/api/console/devices");homePoses=d.home_poses||{};renderHomePoses();renderControls();}catch(_){} }
  directoryPicker=window.CobotPathPicker.create({input:$("deployment-storage"),panel:$("deployment-directory-browser"),list:$("deployment-directory-options"),hint:$("deployment-directory-hint"),fetchDirectories:path=>request("/api/segmented-teach/storage/directories?path="+encodeURIComponent(path)),storage:localStorage,storageKey:"cobot-recent-evaluation-paths",onChange:()=>$("deployment-storage").dataset.dirty="true"});
  $("deployment-storage-browse").addEventListener("click",()=>directoryPicker.refresh());
  recentDirectories=window.CobotPathPicker?.recentSelector?.({input:$("deployment-storage"),id:"deployment-recent-directories",storage:localStorage,storageKey:"cobot-recent-evaluation-paths",extraPaths:()=>state?.recent_data_roots||[],onSelect:()=>$("deployment-storage-apply").click()});
  renderFrames();poll();devices();setInterval(()=>{if(!document.hidden){poll();if(document.querySelector('[data-page="outputs"].active'))refreshOutputs();}},1000);setInterval(()=>{if(document.querySelector('[data-page="deployment"].active'))devices();},15000);
})();
