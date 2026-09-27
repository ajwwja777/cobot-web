"use strict";
(function expose(root){
 let HOME_POSES={front:['plug','origin','high','collect_start','yuheng'],rear:['plug','origin','high','collect_start','yuheng'],all:['plug','origin','high','collect_start','yuheng'],mid:['camera','origin','shuai','camara'],gripper:['reinit']};
 const HOME_SELECTION_KEY='cobot-device-home-selection-v1';
 function readHomeSelection(){try{const value=root.localStorage&&root.localStorage.getItem(HOME_SELECTION_KEY);const parsed=value?JSON.parse(value):{};return parsed&&typeof parsed==='object'?parsed:{};}catch(_error){return {};}}
 function rememberHomeSelection(){try{const target=document.getElementById('device-home-target'),pose=document.getElementById('device-home-pose');if(root.localStorage&&target&&pose&&target.value&&pose.value)root.localStorage.setItem(HOME_SELECTION_KEY,JSON.stringify({target:target.value,pose:pose.value}));}catch(_error){}}
 function tone(job){if(!job)return 'gray';const p=String(job.phase||'');if(p==='ready')return 'green';if(p==='offline'||p==='stopped')return 'gray';if(p==='error')return 'red';if(p==='running'||p==='stopping')return 'amber';if(p==='completed'&&Number(job.exit_code||0)===0)return 'green';if(p==='failed'||p==='stale'||p==='invalid')return 'red';return 'gray';}
 function label(name,job){if(!job)return name+' · idle';const code=job.exit_code==null?'':' ('+job.exit_code+')';const detail=job.detail?' '+job.detail:'';return name.charAt(0).toUpperCase()+name.slice(1)+' · '+String(job.phase||'unknown')+code+detail;}
 function posesFor(target){return (HOME_POSES[String(target)]||[]).slice();}
 async function jsonRequest(path,options={}){const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),10000);try{const response=await fetch(path,{headers:{'Content-Type':'application/json'},...options,signal:controller.signal});const raw=await response.text();let payload={};try{payload=raw?JSON.parse(raw):{};}catch(_error){payload={detail:raw};}if(!response.ok)throw new Error(payload.detail||payload.error||('HTTP '+response.status));return payload;}catch(error){if(error.name==='AbortError')throw new Error('设备请求超时（10 秒），请查看进程日志');throw error;}finally{clearTimeout(timeout);}}
 function setBusy(button,busy){if(!button||!button.classList)return;button.classList.toggle('is-loading',Boolean(busy));button.setAttribute&&button.setAttribute('aria-busy',busy?'true':'false');}
 async function followJob(job,button){
  if(!job||!button)return;
  if(['completed','failed','stopped','stale'].includes(job.phase)){setBusy(button,false);return;}
  for(let count=0;count<60;count++){
   await new Promise(resolve=>setTimeout(resolve,500));
   try{
    const payload=await jsonRequest('/api/console/devices');
    const current=(payload.jobs||{})[job.component];const system=(payload.systems||{})[job.component];
    const serviceReady=['roscore','arms','cameras','rlt'].includes(job.component)&&system&&system.phase==='ready';
    if(serviceReady){setBusy(button,false);message(job.component+' 已就绪。');return;}
    if(current&&current.job_id===job.job_id&&['completed','failed','stopped','stale'].includes(current.phase)){
      setBusy(button,false);
      message(current.phase==='completed'||current.phase==='stopped'
        ? job.component+' 已完成。'
        : job.component+' 未完成：'+String(current.log_tail||current.detail||current.phase).slice(-240),
        current.phase==='failed'||current.phase==='stale');
      return;
    }
   }catch(_error){}
  }
  setBusy(button,false);
  message(job.component+' 状态仍在核验；可查看状态灯和日志。');
 }
 function message(text,error=false){const node=document.getElementById('device-message');if(node){node.textContent=text;node.classList.toggle('error',error);}if(root.CobotWorkspaceUI)root.CobotWorkspaceUI.report(text,error?'error':/正在|启动|等待|停止中/.test(text)?'running':'success','设备');}
 function resultMessage(job){if(job.phase==='stopped')return job.detail||'已停止，状态将变为灰色。';if(job.phase==='stopping')return job.detail||'已发送 Ctrl+C，正在等待进程退出。';if(job.adopted)return '检测到健康的既有进程，已接管停止入口；未重复启动。';if(job.component==='can')return 'CAN 操作已启动；进度和失败接口会显示在下方日志。';return '已启动 '+job.job_id+'；状态和日志会自动刷新。';}
 async function execute(operation,description,confirmed=false,secret=null){const trigger=typeof document!=='undefined'?document.activeElement:null;setBusy(trigger,true);try{const prepared=await jsonRequest('/api/console/devices/confirm',{method:'POST',body:JSON.stringify(operation)});if(!confirmed&&!root.confirm(root.CobotPreferences.text(description+'\n\n该操作只使用登记的固定入口。确认现场状态安全后继续。'))){setBusy(trigger,false);return null;}const actionOperation=secret==null?operation:{...operation,sudo_password:secret};const job=await jsonRequest('/api/console/devices/action',{method:'POST',body:JSON.stringify({operation:actionOperation,confirmation_token:prepared.confirmation_token})});root.CobotOutputPanel?.follow(job);message(resultMessage(job));followJob(job,trigger);return job;}catch(error){setBusy(trigger,false);message('操作未执行：'+error.message,true);return null;}}
 function passwordDialog(title){return new Promise(resolve=>{const dialog=document.createElement('dialog');dialog.className='sudo-dialog';const form=document.createElement('form');form.method='dialog';const heading=document.createElement('h3');heading.textContent=title;const note=document.createElement('p');note.className='subtle';note.textContent='密码仅用于本次 sudo 认证，不写入配置、任务状态或日志。';const input=document.createElement('input');input.type='password';input.autocomplete='current-password';input.required=true;input.placeholder='sudo 密码';input.setAttribute('aria-label','sudo 密码');const buttons=document.createElement('div');buttons.className='button-row sudo-dialog-actions';const cancel=document.createElement('button');cancel.type='button';cancel.className='ghost';cancel.textContent='取消';const submit=document.createElement('button');submit.type='submit';submit.textContent='继续';buttons.append(cancel,submit);form.append(heading,note,input,buttons);dialog.appendChild(form);document.body.appendChild(dialog);let settled=false;function finish(value){if(settled)return;settled=true;input.value='';dialog.close();dialog.remove();resolve(value);}cancel.addEventListener('click',()=>finish(null));dialog.addEventListener('cancel',event=>{event.preventDefault();finish(null);});form.addEventListener('submit',event=>{event.preventDefault();const value=input.value;if(value)finish(value);});dialog.showModal();input.focus();});}
 async function runCan(action){let password=await passwordDialog(action==='reset'?'CAN 重置认证':'CAN 配置认证');if(password==null)return;const operation={component:'can',action,target:'task2'};const description=action==='reset'?'将五臂 CAN 接口复位为 1 Mbps、restart-ms 100；完成后再点配置 CAN 检查链路':'配置 Task2 五臂 CAN；失败时自动复位接口并重试一次';try{await execute(operation,description,false,password);}finally{password=null;}}
 async function runRecover(target){const description=String(target).startsWith('gripper-')?'所选夹爪保持当前开度，执行一次失能清错和原位使能；不设零点、不张开或闭合':'所选机械臂可能短暂失能并恢复 CAN；发送队列堵塞时只自动复位目标接口并重试一次';return execute({component:'recover',action:'run',target},description);}
  function setHomePoses(value){if(!value||typeof value!=='object')return;const before=JSON.stringify(HOME_POSES);for(const target of ['front','rear','all','mid','gripper'])if(Array.isArray(value[target]))HOME_POSES[target]=value[target].map(String);if(typeof document!=='undefined'&&before!==JSON.stringify(HOME_POSES))syncHomePoses();}
 async function waitForJob(job){for(let count=0;count<50;count++){await new Promise(resolve=>setTimeout(resolve,200));const payload=await jsonRequest('/api/console/devices');const current=(payload.jobs||{})[job.component];if(current&&current.job_id===job.job_id&&['completed','failed','stopped','stale'].includes(current.phase))return {payload,current};}throw new Error('Pose 操作等待超时；请查看下方日志');}
 async function runPose(action){const target=document.getElementById('device-home-target').value;const select=document.getElementById('device-home-pose');const input=document.getElementById('device-pose-name');const pose=action==='capture'?String(input.value||'').trim():String(select.value||'');if(target==='gripper'){message('夹爪 reinit 不是可 Capture 的命名位姿。',true);return null;}if(!pose){message('请输入或选择 Pose 名称。',true);return null;}const operation={component:'pose',action,target,pose};const captureDescription=target==='all'?`保存前双臂当前实测位姿为 ${pose}；home all 时后双臂复用对应目标`:`保存 ${target} 当前实测位姿为 ${pose}`;const description=action==='capture'?captureDescription:`删除命名 Pose ${pose} 的全部机械臂记录`;const job=await execute(operation,description);if(!job)return null;try{const done=await waitForJob(job);if(done.current.phase!=='completed')throw new Error(done.current.log_tail||('Pose '+action+' 失败'));setHomePoses(done.payload.home_poses);syncHomePoses(pose);message(action==='capture'?`Pose ${pose} 已保存并加入下拉框。`:`Pose ${pose} 已删除并从下拉框移除。`);return done.current;}catch(error){message('Pose 操作未完成：'+error.message,true);return null;}}
 function insertAfter(anchor,button){anchor.parentNode.insertBefore(button,anchor.nextSibling);}
 function stopButton(anchorId,id,text,operation,description){const anchor=document.getElementById(anchorId);if(!anchor||document.getElementById(id))return null;const button=document.createElement('button');button.id=id;button.type='button';button.className='danger';button.textContent=text;button.addEventListener('click',()=>execute(operation,description));insertAfter(anchor,button);return button;}
 function pairButtons(leftId,rightId){const left=document.getElementById(leftId),right=document.getElementById(rightId);if(!left||!right||left.closest('.device-lifecycle-row'))return;const row=document.createElement('div');row.className='device-lifecycle-row';left.parentNode.insertBefore(row,left);row.append(left,right);}
 const lifecyclePairs=[
  ['roscore','device-roscore','device-roscore-stop','spatial-ros-start','spatial-ros-stop'],
  ['arms','device-arms','device-arms-stop','spatial-arms-start','spatial-arms-stop'],
  ['cameras','device-cameras','device-cameras-stop','spatial-cameras-start','spatial-cameras-stop'],
 ];
 function updateLifecycleControls(payload){
  const systems=payload?.systems||{},jobs=payload?.jobs||{};
  for(const [component,startId,stopId,spatialStartId,spatialStopId] of lifecyclePairs){
   const phase=systems[component]?.phase,jobPhase=jobs[component]?.phase;
   const stopping=jobPhase==='stopping',starting=jobPhase==='running'&&phase!=='ready';
   const canStart=phase==='offline'&&!starting&&!stopping;
   const canStop=!stopping&&(phase==='ready'||phase==='error'||starting);
   for(const id of [startId,spatialStartId]){const button=document.getElementById(id);if(!button)continue;button.disabled=!canStart;button.title=canStart?'可启动':phase==='ready'?'已启动':starting?'启动中':stopping?'停止中':phase==='error'?'节点异常，先停止再启动':'状态未确认';}
   for(const id of [stopId,spatialStopId]){const button=document.getElementById(id);if(!button)continue;button.disabled=!canStop;button.title=canStop?phase==='error'?'节点异常，可停止后重启':'可停止':stopping?'停止中':phase==='offline'?'已停止':'状态未确认';}
  }
  const canBusy=jobs.can?.phase==='running';
  for(const id of ['device-can','device-can-reset','spatial-can','spatial-can-reset']){const button=document.getElementById(id);if(button){button.disabled=canBusy;button.title=canBusy?'CAN 操作进行中':id.endsWith('reset')?'复位接口为 1 Mbps、restart-ms 100；完成后可重新配置':'配置或重试五臂 CAN';}}
 }
 function syncHomePoses(preferred=null){const target=document.getElementById('device-home-target'),pose=document.getElementById('device-home-pose');if(!target||!pose)return;const previous=preferred||pose.value,values=posesFor(target.value);pose.replaceChildren();for(const value of values){const option=document.createElement('option');option.value=value;option.textContent=value==='camara'?'camara（旧标定）':value;pose.appendChild(option);}if(!values.length){const option=document.createElement('option');option.value='';option.textContent='无可用 pose';pose.appendChild(option);}pose.value=values.includes(previous)?previous:(values[0]||'');const name=document.getElementById('device-pose-name');if(name&&!name.matches(':focus'))name.value=pose.value;const home=document.getElementById('device-home'),capture=document.getElementById('device-pose-capture'),remove=document.getElementById('device-pose-delete');if(home)home.disabled=!pose.value;if(capture)capture.disabled=target.value==='gripper';if(remove)remove.disabled=target.value==='gripper'||!pose.value;rememberHomeSelection();}
 function arrangeRlt(){const reference=document.getElementById('device-rlt-reference'),frozen=document.getElementById('device-rlt-frozen'),online=document.getElementById('device-rlt-online'),stop=document.getElementById('device-rlt-stop'),down=document.getElementById('device-rlt-down');if(!reference||!frozen||!online||!stop||!down||reference.closest('.device-rlt-split'))return;const split=document.createElement('div');split.className='device-rlt-split';const starts=document.createElement('div');starts.className='device-rlt-starts';const stops=document.createElement('div');stops.className='device-rlt-stops';reference.parentNode.insertBefore(split,reference);starts.append(reference,frozen,online);stops.append(stop,down);split.append(starts,stops);}
 function mount(){
  const target=document.getElementById('device-home-target'),pose=document.getElementById('device-home-pose');if(target){const saved=readHomeSelection();if(saved.target&&Array.from(target.options||[]).some(option=>option.value===saved.target))target.value=saved.target;target.addEventListener('change',()=>syncHomePoses());if(pose)pose.addEventListener('change',()=>{const name=document.getElementById('device-pose-name');if(name)name.value=pose.value;rememberHomeSelection();});syncHomePoses(saved.pose);}
  const capture=document.getElementById('device-pose-capture'),remove=document.getElementById('device-pose-delete');if(capture)capture.addEventListener('click',()=>runPose('capture'));if(remove)remove.addEventListener('click',()=>runPose('delete'));
  const can=document.getElementById('device-can');
  const statusGrid=document.querySelector('.device-status-grid');
  if(statusGrid&&!document.getElementById('dev-roscore')){
   const status=document.createElement('span');status.id='dev-roscore';status.dataset.tone='gray';status.textContent='ROS Core · idle';statusGrid.insertBefore(status,statusGrid.children[1]||null);
  }
  if(can&&!document.getElementById('device-roscore')){
   const start=document.createElement('button');start.id='device-roscore';start.type='button';start.className='ghost';start.textContent='启动 ROS Core';
   const stop=document.createElement('button');stop.id='device-roscore-stop';stop.type='button';stop.className='danger';stop.textContent='停止 ROS Core';
   start.addEventListener('click',()=>execute({component:'roscore',action:'start'},'启动独立 ROS Core；机械臂与相机随后复用它'));
   stop.addEventListener('click',()=>execute({component:'roscore',action:'stop'},'停止独立 ROS Core；必须先停止机械臂和相机节点'));
   can.parentNode.insertBefore(start,can);can.parentNode.insertBefore(stop,can);pairButtons('device-roscore','device-roscore-stop');
  }
  if(can){can.addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();runCan('configure');},true);const reset=document.createElement('button');reset.id='device-can-reset';reset.type='button';reset.className='danger';reset.textContent='CAN 重置';reset.addEventListener('click',()=>runCan('reset'));insertAfter(can,reset);}
  stopButton('device-arms','device-arms-stop','停止机械臂节点',{component:'arms',action:'stop'},'向所有登记的机械臂 roslaunch 进程组发送 Ctrl+C，并等待退出');
  stopButton('device-cameras','device-cameras-stop','停止三相机',{component:'cameras',action:'stop'},'向所有三相机 roslaunch 进程组发送 Ctrl+C，等待退出并释放 USB');
  pairButtons('device-can','device-can-reset');pairButtons('device-arms','device-arms-stop');pairButtons('device-cameras','device-cameras-stop');
  const recover=document.getElementById('device-recover');if(recover){recover.classList.remove('danger');recover.classList.add('ghost');}
  arrangeRlt();
  updateLifecycleControls({});
 }
 async function runSelection(action,arms,pose){
   const component=action==='home'?'home':'pose';
   const operation={component,action:action==='home'?'run':action,target:'selection',arms,pose};
   const description=action==='delete'
     ? '删除命名位姿 '+pose+' 的全部机械臂记录（配置文件中同名位姿整体删除）'
     : (action==='capture'?'记录位置 ':'归位到 ')+pose+'：'+arms.join('、');
   return execute(operation,description);
 }
 const api={tone,label,posesFor,mount,execute,resultMessage,runRecover,setHomePoses,runPose,runSelection,waitForJob,updateLifecycleControls};root.CobotDeviceUI=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;if(typeof document!=='undefined'){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();}
})(typeof globalThis!=='undefined'?globalThis:this);
