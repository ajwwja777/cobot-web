"use strict";
(function(root){
  const $=selector=>document.querySelector(selector);
  let state={},capture={},busy=false,polling=false,mounted=false,modelsKey='',pendingAction='',lastLoaded='',preferredModel='';
  // Estimates from recorded launches, never a timer or a readiness signal.
  root.CobotModelLoading=model=>{
    if(model?.kind==='pi05'||model?.runtime==='normal')return {zh:'正在加载 · 预计约 1–4 分钟',en:'Loading · approximately 1–4 minutes'};
    if(model?.runtime==='rlt'||['rlt','reference','frozen','online'].includes(model?.kind))return {zh:'正在加载 · 预计约 30 秒–1 分钟',en:'Loading · approximately 30 s–1 minute'};
    return {zh:'正在加载 · 预计数分钟',en:'Loading · estimated a few minutes'};
  };
  const english=()=>root.CobotPreferences?.language==='en';
  root.CobotModelChoiceLabel=(model,{compact=false}={})=>{
    const en=english(),path=model.checkpoint||model.label||model.id;
    const parts=compact?[model.family||model.kind||model.id]:[path,model.family||model.kind||'',model.task||''];
    const step=model.step??null,unknown=en?'steps unknown':'步数待核验';
    if(model.family==='RLT'||model.kind==='rlt'){
      parts.push('stage1-'+(model.stage1_step??model.base_step??'?'));
      if(model.stage!=='stage1')parts.push((model.stage||'warmup')+'-'+(step??'?'));
      if(model.actor_version!=null&&model.actor_version>=0)parts.push('actor-'+model.actor_version);
    }else if(model.parent_step!=null){
      parts.push('DAgger '+model.parent_step+'+'+(step??'?'));
    }else parts.push(step==null?unknown:('step-'+step));
    const status=(root.CobotModelPicker?.available(model)??model.available)?(en?'loadable':'可加载'):
      model.availability==='cli_only'?(en?'CLI available · web control pending':'终端可用 · 网页控制待接入'):
      model.availability==='base_model'?(en?'base dependency':'基础模型依赖'):
      model.availability==='missing_files'?(en?'unavailable · missing files':'不可用 · 缺文件'):
      (en?'unavailable · unregistered':'不可用 · 未登记');
    return parts.filter(Boolean).join(' · ')+' · '+status;
  };
  const enabled=()=>Boolean($('#capture-use-model')?.checked);
  const active=()=>Boolean(state.active)||['recording','paused','finalizing'].includes(capture.capture_state);
  function ready(){return !busy&&!state.operation&&!state.status_stale&&['ready','paused'].includes(state.phase)&&state.model?.id===$('#capture-model-select')?.value;}
  function render(){
    if(!mounted)return;
    const use=enabled(),locked=active(),select=$('#capture-model-select');
    $('#capture-model-actions').hidden=!use;
    $('#capture-use-model').disabled=locked||busy||Boolean(state.operation);
    select.disabled=!use||locked||busy||Boolean(state.operation);
    const loaded=state.model?.id===select.value&&!['offline','error'].includes(state.phase);
    $('#capture-model-load').disabled=locked||busy||Boolean(state.operation)||loaded||!select.value||!state.models?.find(m=>m.id===select.value)?.available;
    $('#capture-model-unload').disabled=locked||busy||Boolean(state.operation)||!state.model||state.phase==='offline';
    $('#capture-session-start').disabled=locked||!ready()||Boolean(state.session_active);
    $('#capture-session-stop').disabled=locked||busy||Boolean(state.operation)||!state.session_active;
    $('#capture-model-state').textContent=!use?(english()?'Manual capture':'纯示教'):state.operation?(english()?'Working…':'正在处理…'):ready()?(english()?'Model ready':'模型已加载'):state.phase==='loading'?(english()?'Loading model…':'模型加载中…'):state.error||(english()?'Load model to start':'等待加载模型');
    const start=$('#start');if(start){start.dataset.zh=use?'开始推理':'开始采集';start.dataset.en=use?'Start inference':'Start capture';start.textContent=english()?start.dataset.en:start.dataset.zh;}
    root.CobotUnifiedCollection?.render();
  }
  async function refresh(){
    if(polling||document.hidden)return;polling=true;
    try{
      const response=await fetch('/api/collection/model',{cache:'no-store'});
      state=await root.CobotConsoleUI.parseApiResponse(response);
      const models=(state.models||[]),key=JSON.stringify([english(),models]),select=$('#capture-model-select');
      if(key!==modelsKey||!select.options.length){modelsKey=key;const keep=preferredModel||select.value||localStorage.getItem('cobot-capture-model')||state.selected_model||state.model?.id;select.replaceChildren(...models.map(m=>{const o=document.createElement('option');o.value=m.id;o.textContent=root.CobotModelChoiceLabel(m);o.disabled=!(root.CobotModelPicker?.available(m)??m.available);return o;}));if(models.some(m=>m.id===keep)){select.value=keep;preferredModel="";}}
      if(!state.status_stale&&['ready','paused','running'].includes(state.phase)&&state.model){
        const key=state.model.id+':'+state.started_at;
        if(lastLoaded!==key){lastLoaded=key;root.CobotWorkspaceUI?.report((english()?'Model loaded: ':'模型加载成功：')+state.model.checkpoint,'success',english()?'Model':'模型');}
      }
    }catch(error){state={...state,status_stale:true,error:error.message};}
    finally{polling=false;render();root.updateButtons?.();}
  }
  async function action(name,modelId){
    if(busy)return;busy=true;pendingAction=name;render();
    const label={load:'加载模型',unload:'释放模型',session_start:'开始 Session',session_stop:'结束 Session'}[name];
    root.CobotWorkspaceUI?.report(label+'…','running',label);
    try{
      const response=await fetch('/api/collection/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,model_id:modelId||$('#capture-model-select').value,...(name==='load'&&root.CobotUnifiedCollection?.executionOptions?{execution_options:root.CobotUnifiedCollection.executionOptions}:{})})});
      state=await root.CobotConsoleUI.parseApiResponse(response);
      root.CobotOutputPanel?.follow({id:"deployment",component:"deployment"});
    }catch(error){root.CobotWorkspaceUI?.report(error.message,'error',label);}
    finally{busy=false;pendingAction="";await refresh();}
  }
  root.addEventListener("cobot:model-default",event=>{preferredModel=event.detail;modelsKey="";refresh();});
  function mount(){
    if(mounted)return;mounted=true;
    $('#capture-use-model').checked=localStorage.getItem('cobot-capture-use-model')==='true';
    $('#capture-use-model').addEventListener('change',()=>{localStorage.setItem('cobot-capture-use-model',enabled());render();root.updateButtons?.();});
    $('#capture-model-select').addEventListener('change',()=>{localStorage.setItem('cobot-capture-model',$('#capture-model-select').value);render();root.updateButtons?.();});
    for(const [id,name] of [['model-load','load'],['model-unload','unload'],['session-start','session_start'],['session-stop','session_stop']])$('#capture-'+id).addEventListener('click',()=>action(name));
    refresh();setInterval(refresh,1500);
  }
  function identity(){const model=(state.models||[]).find(m=>m.id===$('#capture-model-select').value);return model?{task_id:model.task||(model.kind==='rlt'?'plug_insertion':'in_the_pot'),model_id:model.id,checkpoint_id:'step_'+model.step,dataset_round:'dagger_collection'}:{};}
  const loaded=()=>!state.status_stale&&['ready','paused','running'].includes(state.phase)&&state.model?.id===$('#capture-model-select')?.value;
  root.CobotCollectionModel={mount,render,ready,loaded,identity,action,refresh,get state(){return state;},get busy(){return busy||Boolean(state.operation);},get pendingAction(){return pendingAction||state.operation||"";},updateCapture:value=>{capture=value;}};
})(window);
