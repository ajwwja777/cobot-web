"use strict";
(function(root) {
  const arms = ['front-left', 'mid', 'front-right', 'rear-left', 'rear-right'];
  const groups = {
    all: ['front-left','front-right','rear-left','rear-right'],
    five: arms, front: ['front-left','front-right'], rear: ['rear-left','rear-right'],
  };
  const names = {'front-left':['左前臂','Front left'],mid:['中臂','Middle'],
    'front-right':['右前臂','Front right'],'rear-left':['左后臂','Rear left'],'rear-right':['右后臂','Rear right'],
    all:['前后四臂','Four arms'],five:['全部五臂','Five arms'],front:['前双臂','Front pair'],rear:['后双臂','Rear pair'],custom:['自选机械臂','Custom arms'],gripper:['双夹爪','Grippers']};
  const $ = id => root.document.getElementById(id);
  const english = () => root.CobotPreferences?.language === 'en';
  const selectedArms = value => value.target === 'custom' ? arms.filter(a => (value.arms||[]).includes(a)) : (groups[value.target] || (arms.includes(value.target)?[value.target]:[]));
  function posesFor(value, available) {
    if(value.target==='gripper')return available.gripper||[];
    const members=selectedArms(value);
    if(!members.length)return [];
    return (available[members[0]]||[]).filter(pose=>members.every(arm=>(available[arm]||[]).includes(pose)));
  }
  function operation(value, available) {
    if(!value.enabled)return null;
    const members=selectedArms(value);
    if(!members.length && value.target!=='gripper')throw Error('请选择归位机械臂');
    if(!value.pose || !posesFor(value,available).includes(value.pose))throw Error('所选机械臂没有可用的共同位姿，请重新选择');
    return value.target==='gripper'?{component:'home',action:'run',target:'gripper',pose:value.pose}:{component:'home',action:'run',target:'selection',arms:members,pose:value.pose};
  }
  function createPicker(prefix,enabledId,keyPrefix,includeGripper=false) {
    let catalog={},key=keyPrefix,ready=false,locked=false,manualBlocked='等待状态更新';
    let choice={target:'all',pose:'plug2',enabled:true,arms:groups.all.slice()};
  function save() {try{root.localStorage.setItem(key,JSON.stringify(choice));}catch(_){} }
  function render() {
    if(!root.document || !$(prefix+'-target'))return;
    const target=$(prefix+'-target'), pose=$(prefix+'-pose'), lang=english()?1:0;
    if(!target.options.length)for(const value of ['all','five','front','rear',...arms,'custom',...(includeGripper?['gripper']:[])]){
      const option=root.document.createElement('option');option.value=value;target.append(option);
    }
    for(const option of target.options)option.textContent=names[option.value][lang];
    target.value=choice.target;
    $(enabledId).checked=choice.enabled;
    $(enabledId).disabled=locked;
    target.disabled=locked;
    const values=posesFor(choice,catalog);
    // An unavailable saved pose is never silently replaced by another movement target.
    const entries=values.includes(choice.pose)?values:[choice.pose,...values].filter(Boolean);
    const signature=JSON.stringify([entries,values,ready,lang]);
    if(pose.dataset.signature!==signature){
      pose.replaceChildren();
      for(const value of entries){const option=root.document.createElement('option');option.value=value;option.disabled=!values.includes(value);option.textContent=value+(option.disabled?(ready?(lang?' · unavailable':' · 不可用'):(lang?' · checking':' · 核验中')):'');pose.append(option);}
      if(!entries.length){const option=root.document.createElement('option');option.value='';option.textContent=lang?'No shared pose':'无共同位姿';pose.append(option);}
      pose.dataset.signature=signature;
    }
    pose.value=choice.pose;pose.disabled=locked||!values.length;
    const custom=$(prefix+'-arms');custom.hidden=choice.target!=='custom';
    for(const input of custom.querySelectorAll('input')){input.checked=choice.arms.includes(input.value);input.disabled=locked;input.nextElementSibling.textContent=names[input.value][lang];}
    const button=$(prefix+'-now');
    if(button){button.disabled=locked||Boolean(manualBlocked)||!values.includes(choice.pose);button.title=manualBlocked||(!values.includes(choice.pose)?'请选择可用位姿':'');}
  }
  function initialize(profile) {
    key=keyPrefix+':'+profile;
    try{const saved=JSON.parse(root.localStorage.getItem(key)||'null');if(saved && names[saved.target])choice={...choice,...saved,arms:arms.filter(a=>(saved.arms||choice.arms).includes(a))};}catch(_){}
    render();
  }
  function setCatalog(value){catalog=value||{};ready=true;render();}
  function setBusy(value){locked=Boolean(value);render();}
  function setManualBlocked(value){manualBlocked=value||'';render();}
  function selection(){return {...choice,arms:choice.arms.slice()};}
  function mount(){
    if(!$(prefix+'-target'))return;
    const custom=$(prefix+'-arms');custom.replaceChildren();
    for(const arm of arms){const label=root.document.createElement('label'),input=root.document.createElement('input'),text=root.document.createElement('span');input.type='checkbox';input.value=arm;label.append(input,text);custom.append(label);}
    $(prefix+'-target').addEventListener('change',()=>{choice.target=$(prefix+'-target').value;render();save();});
    $(prefix+'-pose').addEventListener('change',()=>{choice.pose=$(prefix+'-pose').value;save();});
    $(enabledId).addEventListener('change',()=>{choice.enabled=$(enabledId).checked;render();save();});
    custom.addEventListener('change',()=>{choice.arms=[...custom.querySelectorAll('input:checked')].map(n=>n.value);render();save();});
    root.document.addEventListener('cobot:language',render);render();
  }
    if(root.document){if(root.document.readyState==='loading')root.document.addEventListener('DOMContentLoaded',mount);else mount();}
    return {initialize,setCatalog,setBusy,setManualBlocked,selection};
  }
  function manualReason(state) {
    const {capture,console:status,session,deployment}=state||{};
    if(!capture||!status||!deployment)return '等待状态更新';
    if(capture.capture_state==='recording')return '请先暂停采集';
    if(!['idle','paused','committed','stopped'].includes(capture.capture_state))return '等待采集结束当前操作';
    if(deployment.status_stale)return '部署状态待确认';
    if(deployment.operation)return '等待部署操作完成';
    if(deployment.phase==='running')return '请先暂停推理';
    if(deployment.active?.intervened)return '请先退出示教';
    if(status.rlt_backend_phase!=='offline'){
      if(!session)return 'RLT 状态待确认';
      if(session.shadow_mode)return '影子模式不执行归位';
      if(session.phase==='hil'||session.expert_mask?.some(Boolean))return '请先退出示教';
      if(session.policy_paused!==true)return '请先暂停推理';
      if(!['paused','waiting_scene','ready','armed','disarmed','stopped','terminal_pending'].includes(session.phase))return '等待本轮提交完成';
    }
    return '';
  }
  async function runManual(options) {
    let reason=manualReason(await options.readState());
    if(reason)throw Error(reason);
    const devices=await options.readDevices();
    const spec=operation({...options.selection,enabled:true},devices.home_poses||{});
    reason=manualReason(await options.readState());
    if(reason)throw Error(reason);
    options.onPhase('homing');
    const job=await options.startHome(spec);
    if(!job)throw Error('归位任务未启动，请查看输出');
    const completed=await options.waitHome(job);
    if(completed?.phase!=='completed')throw Error('归位未完成：'+String(completed?.log_tail||completed?.phase||'unknown').slice(-240));
    options.onPhase('complete');return completed;
  }
  async function run(options) {
    const wait=options.wait||((ms)=>new Promise(resolve=>setTimeout(resolve,ms)));
    const check=()=>{if(options.cancelled?.())throw Error('自动归位已取消');};
    check();
    if(!options.selection.enabled)return;
    let session=options.terminal;
    const identity={session_id:session.session_id,episode_id:session.episode_id};
    options.onPhase('waiting');
    for(let attempt=0;;attempt++){
      check();
      if(!identity.session_id || session.session_id!==identity.session_id || session.episode_id!==identity.episode_id)throw Error('Session 或轮次已变化，未执行自动归位');
      if(session.shadow_mode)throw Error('影子模式不执行归位');
      if(session.phase==='waiting_scene' && session.policy_paused===true)break;
      if(!['terminal_pending','replay_committing'].includes(session.phase))throw Error('本轮未进入暂停的待复位状态，未执行自动归位');
      if(attempt>=120)throw Error('等待本轮提交超时，未执行自动归位');
      await wait(250);session=await options.readSession();
    }
    // Refresh saved pose availability before preparing any robot movement.
    const available=await options.readDevices();check();
    const spec=operation(options.selection,available.home_poses||{});
    session=await options.readSession();check();
    if(session.session_id!==identity.session_id || session.episode_id!==identity.episode_id || session.phase!=='waiting_scene' || session.policy_paused!==true || session.shadow_mode)throw Error('归位前状态已变化，未执行自动归位');
    options.onPhase('homing',spec);
    const job=await options.startHome(spec);check();
    if(!job)throw Error('归位任务未启动，请查看输出');
    for(let attempt=0;attempt<480;attempt++){
      check();
      const devices=await options.readDevices();
      const current=devices.jobs?.home;
      if(current?.job_id===job.job_id){
        if(current.phase==='completed'){options.onPhase('complete',spec);return current;}
        if(['failed','stopped','stale'].includes(current.phase))throw Error('归位未完成：'+String(current.log_tail||current.phase).slice(-240));
      }
      await wait(250);
    }
    throw Error('归位等待超时，请查看输出中的归位任务');
  }
  const api={...createPicker('rlt-home','rlt-home-after-terminal','cobot-rlt-home-v1'),operation,posesFor,run,manualReason,runManual};
  root.CobotCaptureHome=createPicker('capture-home','capture-home-enabled','cobot-post-save-home',true);
  root.CobotRltHome=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
