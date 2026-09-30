"use strict";
(function(root){
  const text=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  const key='cobot-execution-options';
  function create(container,{onChange=()=>{}}={}){
    container.classList.add('execution-options');
    container.innerHTML='<div class="execution-fields"><label><input type="checkbox" class="execution-custom" aria-label="Hz"><span>Hz</span><select class="execution-hz" aria-label="Hz"></select></label><label><input type="checkbox" class="execution-rtc"><span>RTC</span></label><label><input type="checkbox" class="execution-smoothing"><span class="execution-smoothing-label"></span></label><button type="button" class="execution-apply secondary"></button></div><span class="execution-notice" role="status" hidden></span>';
    const custom=container.querySelector('.execution-custom'),hz=container.querySelector('.execution-hz'),rtc=container.querySelector('.execution-rtc'),smoothing=container.querySelector('.execution-smoothing'),noticeNode=container.querySelector('.execution-notice'),apply=container.querySelector('.execution-apply');
    hz.replaceChildren(...[20,30,40,50].map(value=>{const o=document.createElement('option');o.value=value;o.textContent=value;return o;}));
    let model=null,state={},locked=false,busy=false,modelId='',uncertain=false,notice='';
    const defaults=()=>model?.execution_settings||{publish_hz:model?.publish_hz||model?.control_hz||20,rtc:false,smoothing:false};
    const value=()=>{const d=defaults(),v={enabled:true,publish_hz:custom.checked?Number(hz.value):d.publish_hz,rtc:rtc.checked,smoothing:smoothing.checked};return !custom.checked&&v.rtc===Boolean(d.rtc)&&v.smoothing===Boolean(d.smoothing)?{enabled:false}:v;};
    const isLoaded=()=>state.model?.id===model?.id&&state.phase!=='offline';
    function show(){
      container.hidden=!model;
      container.querySelector('.execution-smoothing-label').textContent=text('滤波','Filter');
      custom.disabled=rtc.disabled=smoothing.disabled=locked||busy;
      hz.disabled=locked||busy||!custom.checked;
      apply.textContent=uncertain?text('检查','Check'):text('应用','Apply');
      const changed=JSON.stringify(value())!==JSON.stringify(state.model?.execution_options||{enabled:false});
      apply.hidden=!isLoaded()||model?.kind!=='rlt'||(!changed&&!uncertain);
      apply.disabled=busy||(!uncertain&&(locked||!state.runtime_recovery_available));
      container.title=isLoaded()&&changed&&model?.kind!=='rlt'?text('重新加载模型后生效','Takes effect when the model is reloaded'):'';
      noticeNode.hidden=!notice;noticeNode.textContent=notice;
    }
    function remember(){
      if(locked||busy){show();return;}
      if(!custom.checked)hz.value=String(defaults().publish_hz);
      let saved={};try{saved=JSON.parse(root.localStorage.getItem(key)||'{}');}catch(_){}
      saved[modelId]={...value(),hz_enabled:custom.checked};root.localStorage.setItem(key,JSON.stringify(saved));
      root.dispatchEvent(new CustomEvent('cobot:execution-options',{detail:{modelId,value:saved[modelId],owner:container}}));
      notice='';show();onChange();
    }
    for(const n of [custom,hz,rtc,smoothing])n.addEventListener('change',remember);
    function set(v){const d=defaults();custom.checked=v.hz_enabled??v.enabled===true;hz.value=String(v.publish_hz||d.publish_hz||20);rtc.checked=v.rtc??Boolean(d.rtc);smoothing.checked=v.smoothing??Boolean(d.smoothing);}
    root.addEventListener('cobot:execution-options',event=>{if(event.detail.owner!==container&&event.detail.modelId===modelId&&!locked&&!busy){set(event.detail.value);show();onChange();}});
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
      if(modelId!==model?.id){modelId=model?.id||'';let saved=null;try{saved=JSON.parse(root.localStorage.getItem(key)||'{}')[modelId];}catch(_){}set(saved||state.model?.id===modelId&&state.model.execution_options||{enabled:false});notice='';}
      show();
    },describe(facts=model){
      if(!facts||!model)return facts;
      const v=value(),d=defaults(),settings={...d,publish_hz:v.enabled?v.publish_hz:d.publish_hz,rtc:v.enabled?v.rtc:d.rtc,smoothing:v.enabled?v.smoothing:d.smoothing};
      const active=isLoaded()?state.model.execution_settings:null;
      const pending=active&&['publish_hz','rtc','smoothing'].some(key=>active[key]!==settings[key]);
      return {...facts,execution_settings:settings,active_execution_settings:pending?active:null};
    },get value(){return model?value():undefined;}};
  }
  root.CobotExecutionOptions={create};
})(window);
