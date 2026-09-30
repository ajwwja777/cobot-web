"use strict";
(function(root){
  const text=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  const key='cobot-execution-options';
  function create(container){
    container.classList.add('execution-options');
    container.innerHTML='<label class="execution-enable"><input type="checkbox" class="execution-custom"><span></span></label><div class="execution-fields"><label><span class="execution-hz-label"></span><select class="execution-hz"></select></label><label><input type="checkbox" class="execution-rtc"><span class="execution-rtc-label"></span></label><label><input type="checkbox" class="execution-smoothing"><span class="execution-smoothing-label"></span></label></div><p class="execution-summary" role="status"></p><button type="button" class="execution-apply secondary"></button>';
    const custom=container.querySelector('.execution-custom'),hz=container.querySelector('.execution-hz'),rtc=container.querySelector('.execution-rtc'),smoothing=container.querySelector('.execution-smoothing'),summary=container.querySelector('.execution-summary'),apply=container.querySelector('.execution-apply');
    hz.replaceChildren(...[20,30,40,50].map(value=>{const o=document.createElement('option');o.value=value;o.textContent=value+' Hz';return o;}));
    let model=null,state={},locked=false,busy=false,modelId='',uncertain=false,notice='';
    const value=()=>custom.checked?{enabled:true,publish_hz:Number(hz.value),rtc:rtc.checked,smoothing:smoothing.checked}:{enabled:false};
    const isLoaded=()=>state.model?.id===model?.id&&state.phase!=='offline';
    function show(){
      container.hidden=model?.kind!=='rlt';
      container.querySelector('.execution-enable span').textContent=text('自定义运行选项（异步执行）','Custom execution options (asynchronous)');
      container.querySelector('.execution-hz-label').textContent=text('动作发布 Hz','Action publication Hz');
      container.querySelector('.execution-rtc-label').textContent='RTC';
      container.querySelector('.execution-smoothing-label').textContent=text('因果平滑滤波','Causal smoothing');
      custom.disabled=locked||busy;hz.disabled=rtc.disabled=smoothing.disabled=locked||busy||!custom.checked;
      const defaults=model?.execution_settings||{publish_hz:model?.publish_hz||model?.control_hz||20,logical_hz:20};
      const settings=isLoaded()?(state.session?.execution||state.model.execution_settings||defaults):(custom.checked?{...value(),logical_hz:20}:defaults);
      const activeHz=settings.publish_hz||settings.control_hz||20;
      summary.textContent=(isLoaded()?text('当前实际配置：','Active configuration: '):text('加载时使用：','Configuration for loading: '))+activeHz+' Hz'+text(' 动作发布；逻辑步频 ',' action publication; logical steps ')+(settings.logical_hz||20)+' Hz · RTC '+(settings.rtc??settings.rtc_training_data?'on':'off')+' · '+text('平滑 ','smoothing ')+(settings.smoothing?'on':'off');
      summary.textContent+=' '+text('不勾选自定义时使用所选模型默认设置；滤波关闭仍保留动作与速度限制。','Unchecked custom options use model defaults; turning smoothing off retains action and velocity limits.');
      apply.textContent=uncertain?text('确认运行设置结果','Check configuration result'):text('应用设置（保留 Stage1）','Apply settings (retain Stage1)');
      const changed=JSON.stringify(value())!==JSON.stringify(state.model?.execution_options||{enabled:false});
      apply.hidden=!isLoaded();
      apply.disabled=busy||(!uncertain&&(locked||!changed||!state.runtime_recovery_available));
      if(isLoaded()&&changed)summary.textContent+=' '+text('新选择尚未应用。仅空闲时可重建运行组件应用，随后手动开始。','New choices are pending. Apply while idle by rebuilding runtime components, then start manually.');
      if(notice)summary.textContent+=' '+notice;
    }
    function remember(){
      if(locked||busy){show();return;}
      let saved={};try{saved=JSON.parse(root.localStorage.getItem(key)||'{}');}catch(_){}
      saved[modelId]=value();root.localStorage.setItem(key,JSON.stringify(saved));
      root.dispatchEvent(new CustomEvent('cobot:execution-options',{detail:{modelId,value:value(),owner:container}}));
      notice='';show();
    }
    for(const n of [custom,hz,rtc,smoothing])n.addEventListener('change',remember);
    function set(v){custom.checked=v.enabled===true;hz.value=String(v.publish_hz||model?.execution_settings?.publish_hz||20);rtc.checked=v.rtc??model?.execution_settings?.rtc??false;smoothing.checked=v.smoothing??model?.execution_settings?.smoothing??false;}
    root.addEventListener('cobot:execution-options',event=>{if(event.detail.owner!==container&&event.detail.modelId===modelId&&!locked&&!busy){set(event.detail.value);show();}});
    document.addEventListener('cobot:language',show);
    apply.addEventListener('click',async()=>{
      if(apply.disabled)return;
      if(!uncertain&&!root.confirm(text('重建当前 RLT 运行组件以应用设置，保留 Stage1 权重。未保存到 checkpoint 的训练更新可能丢失；完成后手动开始。','Rebuild the RLT runtime to apply these settings while retaining Stage1. Training updates not checkpointed may be lost. Start manually afterwards.')))return;
      busy=true;show();const control=new AbortController(),timer=setTimeout(()=>control.abort(),90000);
      try{
        const response=await fetch(uncertain?'/api/deployment/status':'/api/rlt/recover-runtime',{cache:'no-store',signal:control.signal,...(uncertain?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restart_running:true,execution_options:value()})})});
        const payload=await response.json();if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:JSON.stringify(payload.detail));
        state=payload;uncertain=false;notice=text('设置结果已更新；等待运行就绪后手动开始。','Configuration status updated; wait for readiness and start manually.');
        await root.CobotCollectionModel?.refresh();await root.CobotDeploymentUI?.poll();
      }catch(error){if(control.signal.aborted||error instanceof TypeError){uncertain=true;notice=text('结果待确认，先检查，不重复提交。','Result uncertain. Check before submitting again.');}else notice=error.message;}
      finally{clearTimeout(timer);busy=false;show();}
    });
    return {update(next,nextState={},disabled=false){
      model=next;state=nextState;locked=Boolean(disabled)||Boolean(state.operation)||state.phase==='loading'||Boolean(state.active)||Boolean(state.status_stale);
      if(modelId!==model?.id){modelId=model?.id||'';let saved=null;try{saved=JSON.parse(root.localStorage.getItem(key)||'{}')[modelId];}catch(_){}set(saved||state.model?.id===modelId&&state.model.execution_options||{enabled:false});notice='';}
      show();
    },get value(){return model?.kind==='rlt'?value():undefined;}};
  }
  root.CobotExecutionOptions={create};
})(window);
