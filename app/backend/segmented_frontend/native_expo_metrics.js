/* Native learner metrics only. GET static observer output; never controls training. */
(function(root){
 'use strict';
 const definitions=[
  ['基础 Actor loss','actor_step',['actor_loss']],
  ['Edit Actor loss / Q','critic_step',['edit_actor_loss','edit_q']],
  ['Edit entropy','critic_step',['entropy']],
  ['Edit temperature','critic_step',['temperature','temperature_loss']],
  ['Filter Critic loss','critic_step',['filter_critic_loss']],
  ['Filter Q','critic_step',['filter_q_train_mean','filter_q_target_mean','filter_q_selected_mean','filter_q_pool_mean']],
  ['训练候选采用 Edit 比例','critic_step',['select_ratio_with_edit','select_ratio_without_edit']],
  ['动作 / Edit 幅度','critic_step',['mean_d_actions_norm','mean_edit_scaled_norm']],
  ['梯度范数','critic_step',['critic_grad_norm','actor_grad_norm','filter_critic_grad_norm']],
  ['采样 / 更新耗时（秒）','critic_step',['sampling_seconds','update_seconds']]
 ];
 function create(parent){
  const section=document.createElement('section');section.className='native-expo-metrics';section.hidden=true;
  const title=document.createElement('h3');title.textContent='EXPO 原生训练指标';
  const status=document.createElement('p'),progress=document.createElement('p');
  const grid=document.createElement('div');grid.className='rl-chart-grid';
  const cards=definitions.map(([name,x,keys])=>{const card=document.createElement('article');card.className='rl-chart-card';const h=document.createElement('h4');h.textContent=name;const legend=document.createElement('p');legend.textContent=keys.join(' / ');const plot=document.createElement('div');plot.className='rl-chart-plot';card.append(h,legend,plot);grid.append(card);return {plot,x,keys};});
  const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='全部原生指标（最新一次更新）';const table=document.createElement('table');details.append(summary,table);
  const note=document.createElement('p');note.textContent='直接复用 Learner 更新返回值。候选采用 Edit 比例是训练 batch 统计，不是机械臂执行比例；Actor loss 不是机器人成功率。每组更新采一个状态点，不展示组内每个梯度步。接入前未记录的指标不补值。';
  section.append(title,status,progress,grid,details,note);parent.append(section);
  let model=null,options=null,selected='',busy=false,last=0,timer=null,disposed=false,payload=null;
  function render(){
   section.hidden=model?.runtime_adapter!=='expo_online';if(section.hidden)return;
   const run=(payload?.runs||[]).find(r=>r.model_id===model.id&&(!options||(r.options.rtc_delay===options.rtc_delay&&r.options.edit_scale===options.edit_scale)));
   const age=run?(Date.now()/1000-run.observed_at):Infinity;
   status.textContent=(run?(age<15&&!payload.read_error?'指标观察器已连接':'指标暂未更新，保留历史'):'等待原生训练指标')+' · W&B：暂未配置上传';
   const p=run?.progress||{};progress.textContent=`Critic ${p.critic_updates??'—'} · Actor 更新 ${p.actor_updates??'—'} · 执行版本 ${p.served_actor_version??'—'} · 已发布版本 ${p.published_actor_version??'—'} · Replay ${p.replay_steps??'—'} · 待处理回合 ${p.pending_episodes??'—'} · 待更新组 ${p.pending_update_calls??'—'}`;
   const points=run?.points||[],rows=points.map(p=>({...p.metrics,...p.timings,critic_step:p.critic_step,actor_step:p.actor_step}));
   for(const c of cards)root.CobotRLMonitoring.chart(c.plot,rows,c.x,c.keys.map((key,i)=>({key,label:key,color:['#47c6ed','#f2bf66','#b59cf8','#47cf9b'][i%4]})),{xlabel:c.x==='actor_step'?'基础 Actor 累计更新次数':'Critic 累计更新次数',empty:'等待真实更新记录；不补造历史'});
   table.replaceChildren();for(const [k,v]of Object.entries(points.at(-1)?.metrics||{}).sort()){const row=document.createElement('tr'),name=document.createElement('td'),value=document.createElement('td');name.textContent=k;value.textContent=Number(v).toPrecision(6);row.append(name,value);table.append(row);}
  }
  async function poll(){
   if(disposed||busy||model?.runtime_adapter!=='expo_online'||document.hidden||Date.now()-last<1900)return;
   busy=true;last=Date.now();const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);try{const r=await root.fetch('/native-expo-metrics.json',{cache:'no-store',signal:controller.signal});if(!r.ok)throw Error('unavailable');const p=await r.json();if(p.schema!==1)throw Error('schema');payload=p;}catch(e){if(payload)payload={...payload,read_error:true};}finally{clearTimeout(timeout);busy=false;if(!disposed)render();}
  }
  timer=setInterval(poll,2000);
  return {update(next,opts){model=next;options=opts;const key=JSON.stringify([model?.id,options]);if(key!==selected){selected=key;last=0;}render();poll();},destroy(){disposed=true;clearInterval(timer);section.remove();}};
 }
 root.CobotNativeExpoMetrics={create};
})(window);
