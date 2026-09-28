"use strict";
(function(root){
  const $=s=>document.querySelector(s);
  let mounted=false,context={},changing=false,rltCatalog=[],catalog=[],modelBusy=false,modelActionName='',selected='',initialized=false,directoryPicker=null,recentDirectories=null,lastLoadedKey='';
  const english=()=>root.CobotPreferences?.language==='en';
  const text=(zh,en)=>english()?en:zh;
  const useModel=()=>Boolean($('#capture-use-model')?.checked);
  const selectedModel=()=>catalog.find(m=>m.id===selected);
  const isRlt=()=>Boolean(root.historyIsRlt?.());
  const active=()=>Boolean(context.console?.active_mode)||['recording','paused','finalizing'].includes(context.capture?.capture_state)||['recording_starting','rollout','hil','paused','terminal_pending','finalizing','replay_committing'].includes(context.session?.phase);
  const labelResults=()=>Boolean($('#collection-label-results')?.checked);
  const modelState=()=>root.CobotCollectionModel?.state||{};
  function modelLoaded(){
    const model=selectedModel();
    if(!useModel()||!model)return false;
    const state=modelState();
    return !state.status_stale&&state.model?.id===model.id&&['ready','paused','running'].includes(state.phase);
  }
  function label(node,zh,en){
    if(!node)return;
    const value=text(zh,en);node.dataset.zh=zh;node.dataset.en=en;
    if(node.textContent!==value)node.textContent=value;
  }
  function value(node,value){if(node&&node.textContent!==String(value??'—'))node.textContent=String(value??'—');}
  function shortcutAction(key,state){
    if(key===' '||key==='Spacebar')return state.pause?'pause':state.resume?'resume':null;
    if(key==='ArrowLeft')return state.discard?'discard':null;
    if(key==='ArrowRight')return state.start?'start':state.paused&&state.save?'save':null;
    if(!state.results)return null;
    if(key==='ArrowUp')return state.success?'success':null;
    if(key==='ArrowDown')return state.failure?'failure':null;
    return null;
  }
  function updateCatalog(list){
    if(list)rltCatalog=list.models||[];
    if(!mounted)return;
    catalog=(modelState().models||[]).map(m=>({...m,runtime:m.kind==='rlt'?'rlt':'normal'}));
    const select=$('#collection-model-select');
    if(!initialized&&catalog.length&&context.console){
      const saved=localStorage.getItem('cobot-collection-model-id');
      const fallback=isRlt()?context.console.rlt_model?.id:localStorage.getItem('cobot-capture-model');
      const candidate=({ 'plug_v3-stage1-reference':'plug-v3-reference' })[saved||fallback]||saved||fallback||modelState().model?.id||modelState().selected_model;
      if(catalog.length){
        selected=catalog.find(m=>m.id===candidate)?.id||catalog.find(m=>m.runtime===(isRlt()?'rlt':'normal'))?.id||catalog[0].id;
        if(localStorage.getItem('cobot-capture-use-model')===null&&isRlt())$('#capture-use-model').checked=true;
        initialized=true;
      }
    }
    const signature=JSON.stringify(catalog.map(m=>[m.id,root.CobotModelChoiceLabel(m),m.available]));
    if(select.dataset.signature!==signature){
      select.replaceChildren(...catalog.map(m=>{const option=document.createElement('option');option.value=m.id;option.textContent=root.CobotModelChoiceLabel(m);option.disabled=!m.available;return option;}));
      if(!catalog.length){const option=document.createElement('option');option.value='';label(option,'正在读取模型…','Reading models…');select.append(option);}
      select.dataset.signature=signature;
    }
    select.value=selected;
    const state=modelState(),loadedKey=state.model?.id+':'+state.started_at;
    if(state.model&&state.phase!=='offline'&&catalog.some(m=>m.id===state.model.id)&&loadedKey!==lastLoadedKey){
      lastLoadedKey=loadedKey;selected=state.model.id;select.value=selected;
    }
    const chosen=selectedModel();
    if(chosen&&$('#capture-model-select').value!==chosen.id)$('#capture-model-select').value=chosen.id;
  }
  function source(action){
    const rlt={start:'rlt-start',pause:'rlt-pause',resume:'rlt-resume',marker:'rlt-marker',save:'rlt-save',discard:'rlt-abort',success:'rlt-success',failure:'rlt-failure'};
    const normal={start:'start',pause:'pause',resume:'resume',marker:'marker',save:'stop',discard:'discard',success:'capture-success',failure:'capture-failure'};
    return $('#'+(isRlt()?rlt:normal)[action]);
  }
  function render(next){
    if(next)context={...context,...next};if(!mounted)return;
    updateCatalog();
    const rlt=isRlt(),use=useModel(),chosen=selectedModel(),state=modelState();
    const processing=modelBusy||root.CobotCollectionModel?.busy;
    const loading=root.CobotCollectionModel?.pendingAction==='load'||state.phase==='loading';
    const locked=active()||changing||context.busy||processing||loading;
    const routeReady=!changing&&(use?chosen?.runtime===(rlt?'rlt':'normal'):!rlt);
    $('#capture-use-model').disabled=Boolean(locked);
    $('#collection-model-select').disabled=Boolean(!use||locked);
    $('#collection-label-results').disabled=Boolean(context.busy||changing);
    const backendOnline=state.model&&!['offline'].includes(state.phase);
    $('#collection-load').disabled=!use||!chosen?.available||locked||modelLoaded()||!routeReady;
    $('#collection-unload').disabled=!use||active()||changing||processing||!backendOnline;
    const ready=modelLoaded();
    const estimate=root.CobotModelLoading(chosen);
    label($('#collection-model-state'),!use?'纯示教':loading?estimate.zh:processing?'正在处理…':ready?'模型加载成功':'等待加载模型',!use?'Manual capture':loading?estimate.en:processing?'Working…':ready?'Model loaded successfully':'Load model to start');
    const failure=use&&(state.error||(state.phase==='error'?(state.detail||'模型加载失败'):null));
    if(failure)label($('#collection-model-state'),String(failure),root.CobotPreferences?.text(String(failure))||String(failure));
    $('#collection-model-state').dataset.tone=failure?'error':ready?'ready':'idle';
    $('#collection-session-start').disabled=!use||!routeReady||changing||processing||!ready||active()||Boolean(state.session_active);
    $('#collection-session-stop').disabled=!use||!routeReady||changing||processing||active()||!state.session_active;
    const normalTerminal=Boolean(context.capture?.buttons?.stop)&&!context.busy;
    for(const key of ['success','failure'])$('#capture-'+key).disabled=!normalTerminal||!labelResults();
    for(const key of ['start','pause','resume','marker','save','discard','success','failure']){
      const button=$('#collection-'+key),original=source(key),result=key==='success'||key==='failure';
      button.disabled=!original||original.disabled||original.classList.contains('is-loading')||changing||!routeReady||(result&&!labelResults());
      button.classList.toggle('is-active',Boolean(original?.classList.contains('is-active')));
      button.classList.toggle('is-loading',Boolean(original?.classList.contains('is-loading')));
      const shortcuts={start:'→',pause:text('空格','Space'),resume:text('空格','Space'),save:text('→ 暂停后','→ when paused'),discard:'←',success:'↑',failure:'↓'};
      button.dataset.shortcut=result&&!labelResults()?'':(shortcuts[key]||'');
    }
    const home=$('#capture-home-enabled').checked;
    label($('#collection-start'),use?'开始推理':'开始采集',use?'Start inference':'Start capture');
    label($('#collection-save'),home?'结束保存并复位':'结束并保存',home?'Save and home':'Finish and save');
    label($('#collection-discard'),home?'结束放弃并复位':'结束并放弃',home?'Discard and home':'Discard episode');
    label($('#collection-success'),'成功并复位','Success and home');label($('#collection-failure'),'失败并复位','Failure and home');
    const phase=rlt?(context.session?.phase||'idle'):(context.capture?.capture_state||'idle');
    value($('#collection-state'),root.CobotPreferences?.text(phase)||phase);
    value($('#collection-frame-count'),rlt?(context.recorder?.frames_written??context.recorder?.frames_sampled??0):context.capture?.training_frame_count??0);
    value($('#collection-node-count'),rlt?(context.recorder?.node_count??context.session?.operator_nodes?.length??'—'):context.capture?.node_count??(context.capture?.nodes||[]).length);
    value($('#collection-generation'),rlt?context.session?.generation??0:context.capture?.generation??0);
    const src=rlt?$('#rlt-data-root'):$('#capture-form input[name=data_root]'),input=$('#collection-data-root');
    if(document.activeElement!==input&&!input.dataset.dirty&&input.value!==src.value)input.value=src.value;
    input.disabled=false;directoryPicker?.setDisabled(false);
    $('#collection-storage-browse').disabled=input.disabled;
    $('#collection-storage-use').disabled=Boolean(changing||(!rlt&&active()));
    recentDirectories?.update();recentDirectories?.setDisabled($('#collection-storage-use').disabled);
    value($('#collection-recording-directory'),$(rlt?'#rlt-recording-directory':'#episode-directory').textContent);
    const msg=$(rlt?'#rlt-message':'#message');value($('#collection-message'),msg.textContent);$('#collection-message').classList.toggle('error',msg.classList.contains('error'));
    label($('#episode-browser-title'),'数据','Data');
    $('#episode-live-strip').classList.remove('hidden');
    if(!rlt){
      value($('#episode-live-badge'),root.CobotPreferences?.text(phase)||phase);
      value($('#episode-live-frames'),text(`${context.capture?.training_frame_count||0} 帧`,`${context.capture?.training_frame_count||0} frames`));
      value($('#episode-live-control'),Object.values(context.capture?.teach_mask||{}).some(Boolean)?'HIL':use?text('自主','Autonomous'):text('示教','Teaching'));
      $('#episode-live-dot').classList.toggle('ok',context.capture?.capture_state==='recording');$('#episode-live-dot').classList.remove('error');
    }
    for(const node of document.querySelectorAll('#collection-shortcuts .collection-result-keys'))node.hidden=!labelResults();
  }
  async function changeRuntime(){
    if(changing||active())return;
    const mode=useModel()&&selectedModel()?.runtime==='rlt'?'rlt':'normal';
    changing=true;render();
    try{
      await root.chooseCollection(mode);
      if(isRlt()!==(mode==='rlt'))throw Error(text('采集模式切换失败','Collection mode switch failed'));
      localStorage.setItem('cobot-collection-model-id',selected);localStorage.setItem('cobot-capture-use-model',useModel());localStorage.setItem('cobot-collection-runtime',mode==='rlt'?'rlt':'pi05');
      delete $('#collection-data-root').dataset.dirty;
    }catch(error){root.CobotWorkspaceUI?.report(error.message,'error',text('采集','Collection'));}
    finally{changing=false;root.CobotCollectionModel?.render();root.updateButtons?.();render();}
  }
  async function modelAction(name){
    const button=$('#collection-'+name);if(button.disabled)return;
    const model=selectedModel();
    modelBusy=true;modelActionName=name;render();
    try{
      await root.CobotCollectionModel.action(name.replace('-','_'),model.id);
      await root.refreshConsole();
    }finally{modelBusy=false;modelActionName="";render();}
  }
  function hideContents(panel){const holder=document.createElement('div');holder.className='collection-legacy';holder.append(...panel.childNodes);panel.append(holder);return holder;}
  function mount(){
    if(mounted)return;mounted=true;
    const page=$('[data-page=operation]'),grid=$('.capture-layout'),panel=grid.querySelector('.action-panel'),storage=grid.querySelector('.normal-only:not(.action-panel)');
    page.classList.add('fixed-collection');grid.classList.add('fixed-collection-grid');panel.id='collection-controls';storage.id='collection-storage';
    panel.classList.remove('normal-only','hidden');storage.classList.remove('normal-only','hidden');$('#episode-browser-operation').classList.remove('normal-only','hidden');
    const old=hideContents(panel);hideContents(storage);
    const legacy=document.createElement('div');legacy.className='collection-legacy';page.append(legacy);legacy.append($('.rl-layout'),$('.rl-process-panel'));
    const use=$('#capture-use-model'),card=document.createElement('article');card.className='panel collection-configuration';
    card.innerHTML='<div class="collection-model-row"></div><div class="button-grid collection-model-actions"></div><p id="collection-model-state" class="inline-status" role="status"></p>';
    card.querySelector('.collection-model-row').append(use.closest('label'));
    const select=document.createElement('select');select.id='collection-model-select';select.setAttribute('aria-label','Model');card.querySelector('.collection-model-row').append(select);
    for(const [id,zh,en] of [['load','加载模型','Load model'],['unload','释放模型','Release model'],['session-start','开始 Session','Start session'],['session-stop','结束 Session','End session']]){
      const button=document.createElement('button');button.type='button';button.id='collection-'+id;label(button,zh,en);button.addEventListener('click',()=>modelAction(id));card.querySelector('.collection-model-actions').append(button);
    }
    page.querySelector('.page-heading').after(card);
    panel.insertAdjacentHTML('afterbegin','<div class="panel-head"><h3 data-zh="采集控制" data-en="Collection controls">采集控制</h3><strong class="state-badge" id="collection-state">—</strong></div><div class="mini-stats"><span><small data-zh="帧数" data-en="Frames">帧数</small><strong id="collection-frame-count">0</strong></span><span><small data-zh="节点" data-en="Markers">节点</small><strong id="collection-node-count">0</strong></span><span><small data-zh="版本" data-en="Version">版本</small><strong id="collection-generation">0</strong></span></div><div class="button-grid collection-episode-actions"></div><div class="collection-results"><label class="collection-result-toggle"><input type="checkbox" id="collection-label-results"/><span data-zh="启用成功 / 失败" data-en="Enable success / failure">启用成功 / 失败</span></label><div class="button-grid collection-result-actions"></div></div>');
    const actions=[['start','开始采集','Start capture'],['pause','暂停并打节点','Pause + marker'],['resume','继续并打节点','Resume + marker'],['marker','只打节点','Add marker'],['save','结束并保存','Finish and save'],['discard','结束并放弃','Discard episode'],['success','成功并复位','Success and home'],['failure','失败并复位','Failure and home']];
    for(const [id,zh,en] of actions){
      const button=document.createElement('button');button.id='collection-'+id;button.type='button';label(button,zh,en);if(['discard','failure'].includes(id))button.className='danger';
      button.addEventListener('click',()=>{const original=source(id);if(!button.disabled&&original&&!original.disabled)original.click();});panel.querySelector(id==='success'||id==='failure'?'.collection-result-actions':'.collection-episode-actions').append(button);
    }
    for(const key of ['success','failure']){const button=document.createElement('button');button.id='capture-'+key;button.type='button';old.append(button);button.addEventListener('click',()=>root.withButtonBusy(button,()=>root.finishCapture(false,key,true)));}
    panel.append($('.post-save-home'));$('#stop-home').hidden=true;for(const node of panel.querySelectorAll('.shortcut-line'))node.hidden=true;
    const legend=document.createElement('div');legend.id='collection-shortcuts';legend.className='shortcut-line';
    legend.innerHTML='<span><kbd>→</kbd> <span data-zh="开始 / 暂停后结束并复位" data-en="Start / Finish while paused + home">开始 / 暂停后结束并复位</span></span><span><kbd data-zh="空格" data-en="Space">空格</kbd> <span data-zh="暂停 / 继续" data-en="Pause / Resume">暂停 / 继续</span></span><span><kbd>←</kbd> <span data-zh="放弃并复位" data-en="Discard + home">放弃并复位</span></span><span class="collection-result-keys"><kbd>↑</kbd> <span data-zh="成功并复位" data-en="Success + home">成功并复位</span></span><span class="collection-result-keys"><kbd>↓</kbd> <span data-zh="失败并复位" data-en="Failure + home">失败并复位</span></span>';
    panel.append(legend);const message=document.createElement('p');message.id='collection-message';message.className='inline-status';message.setAttribute('role','status');panel.append(message);
    $('#collection-label-results').checked=localStorage.getItem('cobot-collection-label-results')==='true';$('#collection-label-results').addEventListener('change',()=>{localStorage.setItem('cobot-collection-label-results',labelResults());render();});
    use.addEventListener('change',changeRuntime);
    select.addEventListener('change',()=>{selected=select.value;const model=selectedModel();if(model){$('#capture-model-select').value=model.id;$('#capture-model-select').dispatchEvent(new Event('change'));}changeRuntime();});
    storage.insertAdjacentHTML('afterbegin','<div class="panel-head"><h3 data-zh="录制目录" data-en="Recording directory">录制目录</h3></div><label><span data-zh="目录" data-en="Directory">目录</span><input id="collection-data-root" autocomplete="off" aria-controls="collection-directory-options" role="combobox"/></label><div class="directory-browser" hidden id="collection-directory-browser"><p class="muted" id="collection-directory-hint"></p><div class="path-options" id="collection-directory-options" role="listbox"></div></div><div class="button-row"><button id="collection-storage-browse" type="button" data-zh="浏览" data-en="Browse">浏览</button><button id="collection-storage-use" type="button" data-zh="检查并使用" data-en="Check and use">检查并使用</button></div><p class="path-caption"><span data-zh="当前：" data-en="Current: ">当前：</span><code data-localize id="collection-recording-directory">—</code></p>');
    const input=$('#collection-data-root');
    directoryPicker=root.CobotPathPicker.create({input,panel:$('#collection-directory-browser'),list:$('#collection-directory-options'),hint:$('#collection-directory-hint'),fetchDirectories:path=>root.request('/api/segmented-teach/storage/directories?path='+encodeURIComponent(path)),storage:localStorage,storageKey:'cobot-unified-collection-paths',onChange:()=>{input.dataset.dirty='true';const target=isRlt()?$('#rlt-data-root'):$('#capture-form input[name=data_root]');target.value=input.value;target.dispatchEvent(new Event('input'));}});
    recentDirectories=root.CobotPathPicker.recentSelector?.({input,id:'collection-recent-directories',storage:localStorage,storageKey:'cobot-unified-collection-paths',extraPaths:()=>root.CobotRecordingDirectories||[],onSelect:()=>$('#collection-storage-use').click()});
    document.addEventListener('cobot:storage-used',event=>{if(event.detail.kind==='collection'){directoryPicker.remember(event.detail.path);recentDirectories?.remember(event.detail.path);}});
    $('#collection-storage-browse').addEventListener('click',()=>directoryPicker.refresh());
    $('#collection-storage-use').addEventListener('click',()=>{const target=isRlt()?$('#rlt-data-root'):$('#capture-form input[name=data_root]');target.value=input.value;if(isRlt())target.dispatchEvent(new Event('input'));$(isRlt()?'#rlt-save-storage':'#prepare-storage').click();delete input.dataset.dirty;});
    document.querySelector('[data-view="operation"]')?.addEventListener('click',()=>{if(useModel()&&!active())changeRuntime();});$('#capture-home-enabled').addEventListener('change',()=>render());document.addEventListener('cobot:language',()=>render());render();
  }
  root.CobotUnifiedCollection={mount,render,labelResults,active,changeRuntime,shortcutAction,updateCatalog,get mounted(){return mounted;},get changing(){return changing||modelBusy;},updateRecorder:state=>{context.recorder=state;render();}};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.CobotUnifiedCollection;
})(typeof window!=='undefined'?window:globalThis);
