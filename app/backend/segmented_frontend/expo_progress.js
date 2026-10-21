/* Read-only EXPO lifecycle. Versions are learner-step identities, not publish counts. */
(function(root){
 'use strict';
 const num=x=>typeof x==='number'&&Number.isFinite(x),val=x=>num(x)&&x>=0?String(x):'—';
 const clock=x=>num(x)?new Date(x*1000).toLocaleString('zh-CN',{hour12:false}):'未记录';
 const bytes=x=>num(x)?(x/1048576).toFixed(1)+' MiB':'总量未知';
 const duration=x=>num(x)?(x<60?x.toFixed(1)+' 秒':(x/60).toFixed(1)+' 分钟'):'—';
 const phases={updating:'训练更新中',sampling:'准备训练 batch',waiting_episode:'等待新回合',waiting_replay:'等待 Replay',loading:'加载中',fault:'训练异常',ready:'就绪'};
 function node(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
 function create(parent){
  const section=node('section',undefined,'expo-progress');section.setAttribute('aria-label','EXPO 在线学习进度');
  const head=node('div',undefined,'expo-progress-head'),title=node('h3','EXPO 在线学习进度'),state=node('span',undefined,'expo-progress-status');head.append(title,state);
  const pipeline=node('div',undefined,'expo-pipeline');
  const stages=[['trained','训练完成','learner_step'],['saved','已保存','committed_learner_step'],['published','可供回传','published_actor_version'],['downloaded','下载校验完成','downloaded_actor_version'],['served','本地生效','served_actor_version']].map(([id,label,key])=>{const e=node('article',undefined,'expo-stage');e.dataset.stage=id;const v=node('strong');e.append(node('span',label),v);pipeline.append(e);return {id,key,v};});
  const lag=node('p',undefined,'expo-progress-muted');
  const grid=node('div',undefined,'expo-progress-grid');const train=node('article'),buffer=node('article'),transfer=node('article');
  const trainBody=node('p'),timing=node('p');train.append(node('h4','训练进度'),trainBody,timing);
  const policy=node('p',undefined,'expo-progress-muted');
  const pool=node('p'),stack=node('div',undefined,'expo-buffer-bar'),seedBar=node('span'),onlineBar=node('span');stack.append(seedBar,onlineBar);const recipe=node('p'),actual=node('p','实际抽样组成：未记录；库存比例不等于每个 batch 的实际比例。','expo-progress-muted'),audit=node('p');buffer.append(node('h4','Replay 库存与采样'),pool,stack,recipe,actual,audit,policy);
  const transferHead=node('p'),bar=node('progress');bar.max=100;bar.setAttribute('aria-label','权重下载字节进度');const detail=node('p'),error=node('p',undefined,'expo-progress-error');transfer.append(node('h4','权重回传'),transferHead,bar,detail,error);
  grid.append(train,buffer,transfer);
  const history=node('details'),summary=node('summary','版本时间线'),table=node('table'),thead=node('thead'),header=node('tr'),tbody=node('tbody');
  for(const t of ['版本（Learner 步）','远端发布','下载完成','本地生效'])header.append(node('th',t));thead.append(header);table.append(thead,tbody);history.append(summary,table,node('p','发布时间来自远端；标“观察到”的时间是网页首次观测。未记录的历史不补造。','expo-progress-muted'));
  const footer=node('p','收尾检查：等待训练结束 → 保存最终 checkpoint → 导出最终版本 → 回传并校验 → 释放 GPU。','expo-progress-muted');
  section.append(head,pipeline,lag,grid,history,footer);parent.append(section);
  function update(run,payload={}){
   section.hidden=!run;if(!run)return;
   const p=run.progress||{},d=p.download||{},t=p.transfer||{},s=p.sampling||{},now=Date.now()/1000;
   const staleLimit=['updating','sampling'].includes(p.learner_phase)?Math.max(300,3*(p.timings?.update_seconds||0)+30):30;
   const stale=num(p.updated_at)&&now-p.updated_at>staleLimit,offline=!!p.network_error,observerStale=payload.read_error||(num(run.observed_at)&&now-run.observed_at>15);
   state.textContent=observerStale?'监控同步中断 · 保留历史':offline?'Learner 连接中断 · 保留最后状态':stale?'Learner 状态已过期 · 保留历史':(phases[p.learner_phase]||'等待状态');state.dataset.tone=offline||stale||observerStale?'warning':'normal';
   stages.forEach(x=>x.v.textContent=val(p[x.key]));
   lag.textContent=(num(p.learner_step)&&num(p.served_actor_version)?'本地比训练端落后 '+(p.learner_step-p.served_actor_version)+' 步。 ':'')+'版本号表示 Learner 步数；不表示累计回传次数。'+(p.updated_at?' 上游最后状态：'+clock(p.updated_at):'');
   trainBody.textContent=`Learner ${val(p.learner_step)} 组 · VLA ${val(p.actor_updates)} 步 · Critic ${val(p.critic_updates)} 步`;
   timing.textContent=`最近一组：采样 ${duration(p.timings?.sampling_seconds)} / 更新 ${duration(p.timings?.update_seconds)}。${p.async_mode==='native_async_v1'?`连续训练，无固定总步数；${val(p.ep_timeout_secs)} 秒无新回合后等待。`:''}`;
   pool.textContent=`Replay ${val(p.replay_steps)} 条 = 示教 ${val(p.seed_steps)} + 在线 ${val(p.online_steps)}；在线已入库 ${val(p.replay_online_episodes)} 回合。待上传 ${val(p.pending_upload_episodes)} 回合。`;
   const fraction=s.seed_fraction;stack.hidden=!num(fraction);if(num(fraction)){seedBar.style.width=(fraction*100)+'%';onlineBar.style.width=((1-fraction)*100)+'%';seedBar.textContent='示教 '+(fraction*100).toFixed(1)+'%';onlineBar.textContent='在线 '+((1-fraction)*100).toFixed(1)+'%';}
   recipe.textContent=num(s.critic_draws_per_group)?`每组 Critic 随机有放回抽取 ${s.critic_draws_per_group} 个样本，分 ${s.critic_batches} 批更新；VLA 单独抽取 ${s.actor_batch_size} 个成功样本更新一次。样本可重复，不消耗 Replay。`:'等待采样配置；不推测实际 batch 大小。';
   const a=p.replay_audit,counts=a?.known_online_samples;audit.textContent=counts?`历史回执确认接收 ${a.known_online_episodes} 个合格回合：成功 ${counts.success} 条 / 失败 ${counts.failure} 条；放弃或排除 ${a.excluded_episodes} 回合。累计接收记录，不等于当前 Replay 库存。`:'';
   policy.textContent=a?.policy_samples?'历史接收样本按采集权重分布：'+Object.entries(a.policy_samples).map(([v,n])=>'v'+v+'：'+n+' 条').join(' / ')+'。这不是当前库存或本次抽样比例。':'';
   const names={retrying:'连接失败，等待重试',downloading:'下载中',downloaded:'已校验，等待装载',staging:'GPU 装载中',installed:'已在本地生效',stage_failed:'GPU 装载失败'};
   transferHead.textContent=`请求版本 ${val(d.actor_version)} · ${names[d.state]||'尚无回传记录'}${num(t.percent)?' · '+t.percent.toFixed(1)+'%':''}`;
   if(num(t.percent))bar.value=Math.max(0,Math.min(100,t.percent));else bar.removeAttribute('value');
   detail.textContent=`该版本本地缓存 ${bytes(t.bytes_received)} / ${bytes(t.bytes_total)} · ${num(t.bytes_per_second)?bytes(t.bytes_per_second)+'/s':'速度未知或传输暂停'}${num(t.eta_seconds)?' · 预计剩余 '+duration(t.eta_seconds):''}`;
   error.textContent=d.error||p.install_error||p.network_error||'';
   const versions=new Map();const row=v=>{if(!versions.has(v))versions.set(v,{});return versions.get(v);};
   for(const [v,e]of Object.entries(p.publications||{})){if(Number.isFinite(Number(v)))row(Number(v)).published=clock(e.published_at);}
   for(const e of run.version_events||[]){if(['published','downloaded','served'].includes(e.stage)&&num(e.version)){const r=row(e.version);if(!r[e.stage])r[e.stage]='观察到 '+clock(e.observed_at);}}
   if(num(d.actor_version)){if(d.downloaded_at)row(d.actor_version).downloaded=clock(d.downloaded_at);if(d.installed_at)row(d.actor_version).served=clock(d.installed_at);}
   const installed=p.installed_receipt;if(num(installed?.actor_version)){if(installed.downloaded_at)row(installed.actor_version).downloaded=clock(installed.downloaded_at);if(installed.installed_at)row(installed.actor_version).served=clock(installed.installed_at);}
   tbody.replaceChildren();for(const [version,e]of [...versions.entries()].sort((a,b)=>b[0]-a[0]).slice(0,30)){const tr=node('tr');for(const v of [version,e.published||'未记录',e.downloaded||'未记录',e.served||'未记录'])tr.append(node('td',String(v)));tbody.append(tr);}
   footer.textContent='收尾检查：'+(num(p.learner_step)&&p.committed_learner_step===p.learner_step?'最终训练状态已保存；':'等待最终状态保存；')+(num(p.learner_step)&&p.downloaded_actor_version===p.learner_step?'最新权重已下载校验。':'最新训练权重尚未确认完整回传。')+' 释放 GPU 前需确认最终版本已导出并回传，或已交给独立传输服务。';
  }
  return {update,destroy(){section.remove();}};
 }
 root.CobotExpoProgress={create};
})(window);
