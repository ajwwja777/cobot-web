"use strict";
// Display-only: resolving or showing a hint never calls fetch or executes a command.
(function(root){
  const P='/media/agilex/Getea1/jiaan/projects/cobot-platform/scripts/';
  const $=s=>document.querySelector(s),value=s=>$(s)?.value||'';
  const t=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  const quote=s=>/^[a-zA-Z0-9_./,:=-]+$/.test(String(s))?String(s):"'"+String(s).replace(/'/g,"'\\''")+"'";
  const command=(file,...args)=>[P+file,...args].map(quote).join(' ');
  const shell=(text,note='')=>({kind:t('执行命令','Command'),text,note});
  const api=(method,path,body,note='')=>({kind:t('网页接口','Web API'),text:method+' '+path+(body?'\n'+JSON.stringify(body,null,2):''),note});
  const home=(target,pose)=>target&&pose?command('home.sh',target,'--pose',pose,'--yes'):null;
  const selectNote=()=>t('先选择目标和位姿','Select a target and pose first');
  const aliases={'spatial-can':'device-can','spatial-can-reset':'device-can-reset','spatial-ros-start':'device-roscore','spatial-ros-stop':'device-roscore-stop','spatial-arms-start':'device-arms','spatial-arms-stop':'device-arms-stop','spatial-cameras-start':'device-cameras','spatial-cameras-stop':'device-cameras-stop'};
  const starts={'device-roscore':'roscore_up.sh','device-arms':'arms_up.sh','device-cameras':'cameras_up.sh'};
  const stops={'device-roscore-stop':'roscore','device-arms-stop':'arms','device-cameras-stop':'cameras'};
  const sessionPaths={arm:'session/arm',start:'session/start',pause:'session/pause',resume:'session/resume',stop:'session/stop',next:'episode/next',success:'episode/success',failure:'episode/failure',abort:'episode/abort'};
  function resolve(button){
    const id=aliases[button.id]||button.id;
    if(id==='device-can'||id==='device-can-reset')return shell(command('can_web.sh',id.endsWith('reset')?'reset':'configure'),t('sudo 密码由网页临时传入标准输入','The console supplies the sudo password through stdin'));
    if(starts[id])return shell(command(starts[id]));
    if(stops[id])return api('POST','/api/console/devices/action',{operation:{component:stops[id],action:'stop'}},t('经 confirm 接口确认后，向登记的进程组发送 SIGINT 并等待退出','After API confirmation, send SIGINT to registered process groups and await exit'));
    if(['spatial-home','spatial-capture','spatial-recover','spatial-pose-delete'].includes(id)){
      const selected=root.CobotSpatialUI?.getSelection?.()||[],arms=selected.filter(x=>!x.startsWith('gripper-'));
      const pose=value('#spatial-pose'),name=value('#spatial-capture-name').trim();
      if(id==='spatial-home'){
        if(root.CobotSpatialUI?.selectedTarget()==='gripper')return shell(home('gripper','reinit'));
        return shell(arms.length&&pose?command('home.sh','selected','--targets',arms.join(','),'--pose',pose,'--yes'):selectNote());
      }
      if(id==='spatial-capture')return shell(arms.length&&name?command('home.sh','capture','--targets',arms.join(','),'--pose',name):t('先选择机械臂并填写位姿名称','Select arms and enter a pose name'));
      if(id==='spatial-pose-delete')return shell(pose?command('home.sh','delete','--pose',pose,'--yes'):selectNote(),t('删除该名称下的全部机械臂位姿','Deletes the named pose for all arms'));
      const batch=selected.includes('front-left')&&selected.includes('front-right')?['front-pair',...selected.filter(x=>!['front-left','front-right'].includes(x))]:selected;
      return shell(batch.map(target=>command('recover.sh',target,'--supported')).join('\n')||selectNote(),batch.length>1?t('按行执行；front-pair 内部并行','Execute in order; front-pair recovers both arms in parallel'):'');
    }
    if(id==='device-home')return shell(home(value('#device-home-target'),value('#device-home-pose'))||selectNote());
    if(id==='device-recover')return shell(command('recover.sh',value('#device-recover-target'),'--supported'));
    if(id.startsWith('device-rlt-')||button.matches('.rlt-mode-picker button')){
      const mode=button.matches('.rlt-mode-picker button')?value('#rlt-mode-choice'):id.slice('device-rlt-'.length);
      const model=root.selectedModel?.();
      if(mode==='stop')return shell(command('rlt_stop.sh'));
      if(!model)return shell(t('先选择模型','Select a model first'));
      const v3=model.id.startsWith('plug_v3-');
      if(mode==='down')return shell(command(v3?'rlt_v3_down.sh':'rlt_down.sh'));
      if(v3)return shell(command('rlt_v3_up.sh',mode));
      return shell(mode==='frozen'?command('rlt_demo.sh'):mode==='reference'?command('rlt_up.sh','--reference','--no-record'):command('rlt_up.sh'));
    }
    if(id.startsWith('rlt-')&&sessionPaths[id.slice(4)]){
      const action=id.slice(4),terminal=['success','failure','abort'].includes(action);
      return api('POST','/api/rlt/'+sessionPaths[action],terminal?{home_after_terminal:Boolean($('#rlt-home-after-terminal')?.checked)}:null,t('网页附带当前 episode_id 与 generation','The console includes the current episode_id and generation'));
    }
    if(id==='rlt-model-switch')return api('POST','/api/rlt/model',{model_id:value('#rlt-model-select')});
    if(id==='rlt-save-storage')return api('POST','/api/rlt/storage',{data_root:value('#rlt-data-root').trim()});
    if(id.startsWith('deploy-')){
      const action=id.slice(7),model_id=value('#deployment-model');
      if(action==='load'){
        const m=root.CobotDeploymentUI?.state?.models?.find(m=>m.id===model_id);
        if(!m)return shell(t('先选择模型','Select a model first'));
        const args=m.kind==='rlt'?[model_id,m.checkpoint,P.replace(/scripts\/$/,'runtime/deployment/evaluation.yaml')]:[model_id];
        return shell(command('deployment_run.sh',...args),'POST /api/deployment/action · '+JSON.stringify({action:'load',model_id}));
      }
      if(action==='home')return shell(home(value('#deploy-home-target'),value('#deploy-home-pose'))||selectNote(),root.CobotDeploymentUI?.state?.active?t('先结束并放弃当前评估轮次','First abort the active evaluation trial'):'');
      if(['start','pause','resume','success','failure','abort','unload'].includes(action)){
        let note=t('网页附带所选 model_id 与当前 trial_id','The console includes the selected model_id and current trial_id');
        if(['success','failure','abort'].includes(action)&&$('#deploy-auto-home')?.checked)note+='\n'+(home(value('#deploy-home-target'),value('#deploy-home-pose'))||selectNote());
        return api('POST','/api/deployment/action',{action,model_id},note);
      }
    }
    if(id==='deployment-storage-apply')return api('POST','/api/deployment/storage',{data_root:value('#deployment-storage').trim()});
    if(id==='prepare-storage'||id==='start')return api('POST','/api/segmented-teach/'+(id==='start'?'start':'storage/prepare'),{data_root:value('#capture-form [name=data_root]').trim(),storage_layout:'flat'});
    if(['pause','resume','marker','stop','discard','stop-home'].includes(id)){
      const action=id==='stop-home'?'stop':id;
      const withHome=id==='stop-home'||id==='discard';
      return api('POST','/api/segmented-teach/'+action,null,t('网页附带当前 episode_uuid 与 generation','The console includes the current episode_uuid and generation')+(withHome?'\n'+(home(value('#capture-home-target'),value('#capture-home-pose'))||selectNote()):''));
    }
    if(id==='delete-history'){
      const uuid=value('#episode-history');if(!uuid)return api('DELETE',t('先选择 Episode','Select an episode first'));
      return api('DELETE',root.historyEndpoint?.('/episodes/'+encodeURIComponent(uuid))||'/api/segmented-teach/episodes/'+encodeURIComponent(uuid));
    }
    if(id==='outputs-refresh')return api('GET','/api/console/outputs?history='+Boolean($('#outputs-history')?.checked));
    return null;
  }
  const tooltip=document.createElement('div');tooltip.id='command-hint';tooltip.className='command-hint';tooltip.role='tooltip';tooltip.hidden=true;
  const heading=document.createElement('div'),code=document.createElement('pre'),note=document.createElement('div');heading.className='command-hint-heading';note.className='command-hint-note';tooltip.append(heading,code,note);document.body.append(tooltip);
  let active=null,timer=null,previousTitle=null,previousDescription=null;
  // Only observe the hovered button, since lifecycle refreshes update its status title.
  const titleObserver=new MutationObserver(()=>{if(active?.hasAttribute('title')){previousTitle=active.title;active.removeAttribute('title');}});
  function hide(){clearTimeout(timer);titleObserver.disconnect();if(active){if(previousTitle!==null)active.title=previousTitle;if(previousDescription===null)active.removeAttribute('aria-describedby');else active.setAttribute('aria-describedby',previousDescription);}active=null;tooltip.hidden=true;}
  function position(){if(!active||tooltip.hidden)return;const width=document.documentElement.clientWidth;tooltip.style.maxWidth=Math.min(650,width-16)+'px';const r=active.getBoundingClientRect(),box=tooltip.getBoundingClientRect();let y=r.bottom+8;if(y+box.height>innerHeight-8)y=Math.max(8,r.top-box.height-8);tooltip.style.left=Math.max(8,Math.min(r.left,width-box.width-8))+'px';tooltip.style.top=y+'px';}
  function show(){if(!active?.isConnected)return hide();const hint=resolve(active);if(!hint)return hide();heading.textContent=hint.kind+(active.disabled?' · '+t('当前不可执行','Currently unavailable'):'');code.textContent=hint.text;note.textContent=hint.note||'';note.hidden=!hint.note;tooltip.hidden=false;position();}
  function enter(button,immediate=false){if(button===active)return;if(!resolve(button))return hide();hide();active=button;previousTitle=button.getAttribute('title');previousDescription=button.getAttribute('aria-describedby');button.removeAttribute('title');titleObserver.observe(button,{attributes:true,attributeFilter:['title']});button.setAttribute('aria-describedby',[previousDescription,'command-hint'].filter(Boolean).join(' '));timer=setTimeout(show,immediate?0:240);}
  document.addEventListener('pointerover',e=>{const button=e.target.closest?.('button');if(button)enter(button);else hide();});
  document.addEventListener('pointerout',e=>{if(active&&!active.contains(e.relatedTarget))hide();});
  document.addEventListener('focusin',e=>{const button=e.target.closest?.('button');if(button)enter(button,true);});
  document.addEventListener('focusout',hide);
  document.addEventListener('pointerdown',hide,true);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});
  document.addEventListener('change',()=>{if(active&&!tooltip.hidden)show();});
  document.addEventListener('input',()=>{if(active&&!tooltip.hidden)show();});
  document.addEventListener('cobot:language',()=>{if(active&&!tooltip.hidden)show();});
  root.addEventListener('scroll',hide,true);root.addEventListener('resize',hide);
  root.CobotCommandHints={resolve};
})(globalThis);
