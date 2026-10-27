"use strict";
(function(root){
  const text=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  const key='cobot-execution-options';
  function create(container,{onChange=()=>{}}={}){
    container.classList.add('execution-options');
    container.innerHTML='<div class="execution-fields"><label><input type="checkbox" class="execution-custom" aria-label="Hz"><span>Hz</span><select class="execution-hz" aria-label="Hz"></select></label><label><input type="checkbox" class="execution-rtc"><span>RTC</span></label><label><input type="checkbox" class="execution-smoothing"><span class="execution-smoothing-label"></span></label><button type="button" class="execution-apply secondary"></button></div><div class="execution-expo-fields" hidden><label><span>d</span><select class="execution-delay" aria-label="RTC delay d"></select></label><label><span>Edit scale</span><select class="execution-edit-scale" aria-label="Edit scale"></select></label><span class="execution-expo-hint"></span></div><span class="execution-notice" role="status" hidden></span>';
    const custom=container.querySelector('.execution-custom'),hz=container.querySelector('.execution-hz'),rtc=container.querySelector('.execution-rtc'),smoothing=container.querySelector('.execution-smoothing'),noticeNode=container.querySelector('.execution-notice'),apply=container.querySelector('.execution-apply');
    const expoFields=container.querySelector('.execution-expo-fields'),delay=container.querySelector('.execution-delay'),scale=container.querySelector('.execution-edit-scale'),expoHint=container.querySelector('.execution-expo-hint');
    const fill=(node,values,label)=>node.replaceChildren(...values.map(value=>{const o=document.createElement('option');o.value=String(value);o.textContent=label(value);return o;}));
    fill(delay,Array.from({length:8},(_,i)=>i+1),v=>`${v} (${v*100} ms)`);
    fill(scale,Array.from({length:10},(_,i)=>((i+1)/100).toFixed(2)),v=>v);
    const rates=expo=>expo?[10,20,30,40,50]:[20,30,40,50];
    const setRates=expo=>hz.replaceChildren(...rates(expo).map(value=>{const o=document.createElement('option');o.value=value;o.textContent=value;return o;}));
    setRates(false);
    let model=null,state={},locked=false,busy=false,modelId='',uncertain=false,notice='';
    const defaults=()=>model?.execution_settings||{publish_hz:model?.publish_hz||model?.control_hz||20,rtc:false,smoothing:false};
    const supportsExpo=()=>model?.runtime_adapter==='expo_online'&&Boolean(model?.expo_online_options)&&Boolean(model?.execution_settings?.expo);
    const staged532=()=>model?.runtime_adapter==='expo_online'&&['realtime-expo-ft-532-batch5','realtime-expo-ft-532-hpc4'].includes(model?.training_method);
    const hasExpoFields=()=>supportsExpo()||staged532();
    const draftExpo=()=>({rtc_delay:Number(delay.value),edit_scale:Number(scale.value)});
    const expoDefaults=()=>model?.expo_online_options?.defaults||{rtc_delay:5,edit_scale:0.01};
    const value=()=>{if(model?.execution_options_supported===false)return {enabled:false};const d=defaults(),v={enabled:true,publish_hz:custom.checked?Number(hz.value):d.publish_hz,rtc:rtc.checked,smoothing:smoothing.checked};if(supportsExpo())v.expo={rtc_delay:Number(delay.value),edit_scale:Number(scale.value)};return !supportsExpo()&&!custom.checked&&v.rtc===Boolean(d.rtc)&&v.smoothing===Boolean(d.smoothing)?{enabled:false}:v;};
    const isLoaded=()=>state.model?.id===model?.id&&state.phase!=='offline';
    function show(){
      container.hidden=!model;
      container.querySelector('.execution-smoothing-label').textContent=text('滤波','Filter');
      custom.disabled=rtc.disabled=smoothing.disabled=locked||busy||model?.execution_options_supported===false;
      if(model?.runtime_adapter==='expo_online'){rtc.checked=true;rtc.disabled=true;rtc.title=text('在线训练使用 RTC 延迟条件','Online learning uses RTC delay conditioning');}else rtc.title='';
      hz.disabled=locked||busy||!custom.checked||model?.execution_options_supported===false;
      expoFields.hidden=!hasExpoFields();
      delay.disabled=scale.disabled=staged532()?busy:locked||busy||isLoaded();
      expoHint.textContent=text('加载时生效 · Actor / Learner 同步','Applied on load · Actor / Learner synchronized');
      if(staged532()){
        const live=isLoaded()?(state.online||state.model.execution_settings?.expo||expoDefaults()):null;
        const current=live?`d ${live.rtc_delay??5} / Edit ${Number(live.edit_scale??.01).toFixed(2)}`:text('未加载','Not loaded');
        expoHint.textContent=text('当前：','Active: ')+current+' · '+(supportsExpo()?text('选择自动保存，下次加载应用','Selections saved for next load'):text('选择仅保存为待应用；HPC固定进程需另行同步，当前加载不会应用新值','Draft only; fixed HPC learner needs separate synchronization; loading currently does not apply new values'));
      }
      delay.title=text('10 Hz 逻辑步，每步 100 ms；当前默认 5','10 Hz logical steps, 100 ms per step; default 5');
      scale.title=text('同时用于采样与学习；更改参数须释放后重新加载','Used for sampling and learning; release and reload to change');
      apply.textContent=uncertain?text('检查','Check'):text('应用','Apply');
      const changed=JSON.stringify(value())!==JSON.stringify(state.model?.execution_options||{enabled:false});
      apply.hidden=!isLoaded()||model?.kind!=='rlt'||(!changed&&!uncertain);
      apply.disabled=busy||(!uncertain&&(locked||!state.runtime_recovery_available));
      container.title=isLoaded()&&changed&&model?.kind!=='rlt'?text('重新加载模型后生效','Takes effect when the model is reloaded'):'';
      noticeNode.hidden=!notice;noticeNode.textContent=notice;
    }
    function remember(event){
      const staging=staged532()&&[delay,scale].includes(event?.target);
      if((locked&&!staging)||busy){show();return;}
      if(!custom.checked)hz.value=String(defaults().publish_hz);
      let saved={};try{saved=JSON.parse(root.localStorage.getItem(key)||'{}');}catch(_){}
      saved[modelId]={...value(),...(hasExpoFields()?{expo:draftExpo()}:{}),hz_enabled:custom.checked};root.localStorage.setItem(key,JSON.stringify(saved));
      root.dispatchEvent(new CustomEvent('cobot:execution-options',{detail:{modelId,value:saved[modelId],owner:container}}));
      notice='';show();onChange();
    }
    for(const n of [custom,hz,rtc,smoothing,delay,scale])n.addEventListener('change',remember);
    function set(v){const d=defaults();custom.checked=v.hz_enabled??v.enabled===true;hz.value=String(v.publish_hz||d.publish_hz||20);rtc.checked=v.rtc??Boolean(d.rtc);smoothing.checked=v.smoothing??Boolean(d.smoothing);const e=v.expo||expoDefaults();delay.value=String(e.rtc_delay);scale.value=Number(e.edit_scale).toFixed(2);}
    root.addEventListener('cobot:execution-options',event=>{if(event.detail.owner!==container&&event.detail.modelId===modelId&&(!locked||staged532())&&!busy){set(event.detail.value);show();onChange();}});
    document.addEventListener('cobot:language',show);
    apply.addEventListener('click',async()=>{
      if(apply.disabled)return;
      if(!uncertain&&!root.confirm(text('重建运行组件并保留 Stage1。未保存的训练更新可能丢失；完成后手动开始。','Rebuild runtime components and retain Stage1. Unsaved training updates may be lost; start manually afterwards.')))return;
      busy=true;show();const control=new AbortController(),timer=setTimeout(()=>control.abort(),90000);
      try{
        const response=await fetch(uncertain?'/api/deployment/status':'/api/rlt/recover-runtime',{cache:'no-store',signal:control.signal,...(uncertain?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restart_running:true,execution_options:value()})})});
        const payload=await response.json();if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:JSON.stringify(payload.detail));
        state=payload;uncertain=false;notice='';
        await root.CobotCollectionModel?.refresh();await root.CobotDeploymentUI?.poll();
      }catch(error){if(control.signal.aborted||error instanceof TypeError){uncertain=true;notice=text('结果待确认，点击检查。','Result pending; select Check.');}else notice=error.message;}
      finally{clearTimeout(timer);busy=false;show();}
    });
    return {update(next,nextState={},disabled=false){
      model=next;state=nextState;locked=Boolean(disabled)||Boolean(state.operation)||state.phase==='loading'||Boolean(state.active)||Boolean(state.status_stale);
      if(modelId!==model?.id){setRates(['expo_left','expo_online'].includes(model?.runtime_adapter));modelId=model?.id||'';let saved=null;try{saved=JSON.parse(root.localStorage.getItem(key)||'{}')[modelId];}catch(_){}set(saved||state.model?.id===modelId&&state.model.execution_options||{enabled:false});notice='';}
      if(isLoaded()&&supportsExpo()&&!staged532()) {const e=state.model.execution_options?.expo||state.model.execution_settings?.expo||expoDefaults();delay.value=String(e.rtc_delay);scale.value=Number(e.edit_scale).toFixed(2);}
      if(model?.execution_options_supported===false)set({enabled:false});
      show();
    },describe(facts=model){
      if(!facts||!model)return facts;
      const v=value(),d=defaults(),settings={...d,publish_hz:v.enabled?v.publish_hz:d.publish_hz,rtc:v.enabled?v.rtc:d.rtc,smoothing:v.enabled?v.smoothing:d.smoothing};
      if(supportsExpo())settings.expo=isLoaded()&&staged532()?(state.model.execution_settings?.expo||expoDefaults()):value().expo;
      const active=isLoaded()?state.model.execution_settings:null;
      const pending=active&&['publish_hz','rtc','smoothing'].some(key=>active[key]!==settings[key]);
      return {...facts,execution_settings:settings,active_execution_settings:pending?active:null};
    },get value(){return model?value():undefined;}};
  }
  root.CobotExecutionOptions={create};
})(window);
