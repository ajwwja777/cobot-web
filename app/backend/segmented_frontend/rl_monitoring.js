/* EXPO / RLT read-only monitoring. No control, model or recorder operations. */
(function(root){
  'use strict';
  const ns='http://www.w3.org/2000/svg';
  const number=v=>typeof v==='number'&&Number.isFinite(v);
  const english=()=>root.CobotPreferences?.language==='en';
  const tr=(zh,en)=>english()?en:zh;
  const fmt=v=>!number(v)?'—':Math.abs(v)>=10000?v.toPrecision(3):Math.abs(v)>0&&Math.abs(v)<.001?v.toExponential(2):Number(v.toFixed(4)).toString();
  function svgNode(name,attrs,text){const n=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attrs||{}))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
  function chart(host,rows,xkey,series,{percent=false,nonnegative=false,integerX=true,xlabel='',empty=''}={}){
    host.replaceChildren();
    const valid=rows.filter(p=>number(p[xkey])&&series.some(s=>number(p[s.key])));
    if(!valid.length){const p=document.createElement('p');p.className='rl-chart-empty';p.textContent=empty;host.append(p);return;}
    const W=600,H=240,L=62,R=18,T=16,B=46,pw=W-L-R,ph=H-T-B;
    let xmin=Math.min(...valid.map(p=>p[xkey])),xmax=Math.max(...valid.map(p=>p[xkey]));
    if(xmin===xmax){xmin=Math.max(0,xmin-1);xmax+=1;}
    const ys=valid.flatMap(p=>series.map(s=>p[s.key]).filter(number));
    let ymin=percent?0:Math.min(...ys),ymax=percent?100:Math.max(...ys);
    if(!percent){const pad=Math.max((ymax-ymin)*.1,Math.abs(ymax)*.03,1e-6);ymin-=pad;ymax+=pad;}
    if(nonnegative)ymin=Math.max(0,ymin);
    const x=v=>L+(v-xmin)/(xmax-xmin)*pw,y=v=>T+(ymax-v)/(ymax-ymin)*ph;
    const svg=svgNode('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':host.dataset.label||xlabel});
    const seenTicks=new Set();
    for(let i=0;i<5;i++){
      const yy=T+i*ph/4,value=ymax-(ymax-ymin)*i/4;
      svg.append(svgNode('line',{x1:L,y1:yy,x2:W-R,y2:yy,class:'rl-chart-gridline'}));
      svg.append(svgNode('text',{x:L-8,y:yy+4,'text-anchor':'end',class:'rl-chart-tick'},percent?Math.round(value)+'%':fmt(value)));
      const raw=xmin+(xmax-xmin)*i/4,tick=integerX?Math.round(raw):raw;
      if(!seenTicks.has(tick)){seenTicks.add(tick);svg.append(svgNode('text',{x:x(tick),y:H-B+20,'text-anchor':'middle',class:'rl-chart-tick'},fmt(tick)));}
    }
    svg.append(svgNode('text',{x:L+pw/2,y:H-5,'text-anchor':'middle',class:'rl-chart-axis'},xlabel));
    for(const s of series){
      let d='',pen=false;
      for(const p of rows){if(!number(p[xkey])||!number(p[s.key])){pen=false;continue;}d+=(pen?' L':' M')+x(p[xkey])+','+y(p[s.key]);pen=true;}
      svg.append(svgNode('path',{d,fill:'none',stroke:s.color,'stroke-width':2,'stroke-dasharray':s.dash||'','vector-effect':'non-scaling-stroke'}));
      for(const p of valid.filter(p=>number(p[s.key]))){
        const dot=svgNode('circle',{cx:x(p[xkey]),cy:y(p[s.key]),r:valid.length===1?4:3,fill:s.color,class:'rl-chart-point',tabindex:0});
        dot.append(svgNode('title',{},`${xlabel}: ${fmt(p[xkey])}\n${s.label}: ${fmt(p[s.key])}${percent?'%':''}${p.total?`\n${p.successes}/${p.total}`:''}${p.actor_version!==undefined?`\nActor: ${p.actor_version}`:''}`));svg.append(dot);
      }
    }
    host.append(svg);
  }
  const definitions=()=>[
    ['q',tr('Q 学习趋势','Q learning trend'),tr('平均 Q（蓝） · 平均 TD target（黄虚线）','Mean Q (blue) · Mean TD target (yellow dashed)')],
    ['loss',tr('Critic 学习误差','Critic loss'),tr('TD 拟合均方误差','TD fitting mean squared error')],
    ['success',tr('自主成功率','Autonomous success rate'),tr('最近 20 个有效完成回合','Last 20 valid completed attempts')],
    ['rollout',tr('单轮实时 Q','Episode Q'),tr('实际选中动作块的价值估计','Value of the selected action chunk')]
  ];
  function create(after){
    const section=document.createElement('section');section.id='collection-rl-monitoring';section.className='panel rl-monitoring';section.hidden=true;
    const heading=document.createElement('div');heading.className='panel-head';
    const title=document.createElement('h3'),badge=document.createElement('span');badge.className='rl-monitor-badge';heading.append(title,badge);
    const meta=document.createElement('p');meta.className='rl-monitor-meta';
    const grid=document.createElement('div');grid.className='rl-chart-grid';
    const cards={};for(const [key]of definitions()){
      const card=document.createElement('article');card.className='rl-chart-card';
      const head=document.createElement('div');head.className='rl-chart-head';const name=document.createElement('h4'),last=document.createElement('span');head.append(name,last);
      const subtitle=document.createElement('p'),plot=document.createElement('div');plot.className='rl-chart-plot';
      card.append(head,subtitle,plot);grid.append(card);cards[key]={name,last,subtitle,plot};
    }
    const episodePicker=document.createElement('select');episodePicker.className='rl-episode-picker';episodePicker.setAttribute('aria-label','查看回合 / Select episode');cards.rollout.subtitle.after(episodePicker);
    const note=document.createElement('p');note.className='rl-monitor-note';section.append(heading,meta,grid,note);after.after(section);
    const nativeMetrics=root.CobotNativeExpoMetrics?.create(section);
    let rolloutSelection='',selection='',supported=false,model=null,state={},options=null,payload=null,inflight=null,lastPoll=0,error='',timer=null;
    episodePicker.addEventListener('change',()=>{rolloutSelection=episodePicker.value;inflight?.abort();inflight=null;lastPoll=0;if(payload)payload={...payload,rollout:{}};render();poll();});
    function render(){
      title.textContent=tr('RL 在线学习','RL online learning');
      const learner=payload?.learner;
      const rlt=model?.kind==='rlt';
      let connection=payload?.live?tr('已连接','Connected'):tr('历史 / 等待连接','History / awaiting connection');
      if(learner?.supported){
        connection=learner.connected?(learner.running?tr('Learner 运行中','Learner running'):tr('训练已停止 · 历史记录','Learner stopped · history')):
          learner.loading?tr('正在连接 Learner','Connecting to learner'):tr('指标连接中断 · 保留曲线','Metrics disconnected · history retained');
        if(learner.connected&&learner.phase==='stale')connection=tr('Learner 暂无心跳 · 历史记录','No learner heartbeat · history');
        if(learner.connected&&learner.running&&!payload?.actor_loaded)connection+=tr(' · Actor 未加载',' · Actor unloaded');
      }
      badge.textContent=!supported?tr('当前模式无在线学习','No online learning in this mode'):error?tr('网页连接中断 · 保留曲线','Web disconnected · history retained'):connection;
      badge.dataset.live=String(Boolean(payload?.live&&!error));
      const o=payload?.options||options||{},t=payload?.training?.updated_at;
      meta.textContent=[model?.label||model?.id,...(rlt?[]:[`d ${o.rtc_delay??'—'}`,`Edit ${fmt(o.edit_scale)}`]),`Actor ${payload?.actor_version??'—'}`,`Learner ${learner?.learner_version??'—'}`,tr('最近学习记录：','Last learning sample: ')+(t?new Date(t*1000).toLocaleString():tr('尚无','none'))].join(' · ');
      for(const [key,name,sub]of definitions()){cards[key].name.textContent=name;cards[key].subtitle.textContent=sub;cards[key].plot.dataset.label=name;}
      if(rlt){cards.q.subtitle.textContent=tr('平均 Q1（蓝） · 平均 TD target（黄虚线）','Mean Q1 (blue) · Mean TD target (yellow dashed)');cards.loss.subtitle.textContent=tr('Q1 与 Q2 的 TD 均方误差之和','Sum of Q1 and Q2 TD mean squared errors');}
      if(rlt&&payload?.target_semantics==='training_target')cards.q.subtitle.textContent=tr('平均 Q1（蓝） · 实际训练 target（黄虚线）','Mean Q1 (blue) · Actual training target (yellow dashed)');
      const points=payload?.training?.points||[],last=points.at(-1),updateAxis=tr('Critic 累计更新次数','Cumulative critic updates');
      cards.q.last.textContent=`Q ${fmt(last?.q)} / target ${fmt(last?.target_q_mean)}`;
      cards.loss.last.textContent=fmt(last?.critic_loss);
      const awaiting=!supported?tr('选择 EXPO / RLT Online 模式后显示学习记录','Select EXPO / RLT Online to view learning records'):tr('等待真实学习记录；未记录的历史不补值','Awaiting real learning samples; no historical values inferred');
      chart(cards.q.plot,points,'critic_step',[{key:'q',label:'Q',color:'#47c6ed'},{key:'target_q_mean',label:payload?.target_semantics==='training_target'?'Training target':'TD target',color:'#f2bf66',dash:'5 4'}],{xlabel:updateAxis,empty:awaiting});
      chart(cards.loss.plot,points,'critic_step',[{key:'critic_loss',label:'Critic loss',color:'#b59cf8'}],{nonnegative:true,xlabel:updateAxis,empty:awaiting});
      const successes=payload?.success||{},sp=successes.points||[],sl=sp.at(-1);
      cards.success.last.textContent=sl?`${fmt(sl.rate)}% · ${sl.successes}/${sl.total}`:'—';
      cards.success.subtitle.textContent=sl?tr(`最近 ${sl.total} 轮 · 含接管 ${sl.assisted} 轮`,`Last ${sl.total} attempts · ${sl.assisted} assisted`):definitions()[2][2];
      chart(cards.success.plot,sp,'episode',[{key:'rate',label:tr('自主成功率','Autonomous success rate'),color:'#47cf9b'}],{percent:true,xlabel:tr('已完成在线回合编号','Completed online episode number'),empty:successes.error?tr('回合记录读取失败，等待重试','Episode history unavailable; retrying'):successes.loading?tr('正在读取真实回合记录…','Reading episode history…'):tr('暂无有效完成回合','No valid completed episodes')});
      const choices=payload?.rollout_episodes||[];
      episodePicker.replaceChildren();
      const currentOption=document.createElement('option');currentOption.value='';currentOption.textContent=tr('跟随当前 / 最近回合','Follow current / latest episode');episodePicker.append(currentOption);
      for(const item of choices){const option=document.createElement('option');option.value=item.episode_uuid;option.textContent=(item.started_at?new Date(item.started_at*1000).toLocaleString()+' · ':'')+item.episode_uuid.slice(0,8)+` · Actor ${item.actor_version??'—'}`;episodePicker.append(option);}
      if(rolloutSelection&&!choices.some(p=>p.episode_uuid===rolloutSelection)){const option=document.createElement('option');option.value=rolloutSelection;option.textContent=rolloutSelection.slice(0,8);episodePicker.append(option);}
      episodePicker.value=rolloutSelection;episodePicker.disabled=!supported;
      const rollout=payload?.rollout||{},rp=rollout.points||[];
      cards.rollout.last.textContent=fmt(rp.at(-1)?.q);
      if(rollout.episode_uuid)cards.rollout.subtitle.textContent=(rolloutSelection?tr('历史回合','Historical episode'):rollout.active?tr('当前回合','Current episode'):tr('最近记录回合','Last recorded episode'))+' · '+rollout.episode_uuid.slice(0,8)+` · Actor ${rollout.actor_version??'—'}`;
      if(rlt&&rollout.episode_uuid)cards.rollout.subtitle.textContent+=` · Critic ${rollout.critic_version??'—'}`+tr('（本轮固定）',' (pinned)');
      chart(cards.rollout.plot,rp,'seconds',[{key:'q',label:'Selected Q',color:'#ef94be'}],{integerX:false,xlabel:tr('本回合开始后的时间（秒）','Seconds since episode start'),empty:rollout.error?tr('Q 评分暂不可用：','Q scoring unavailable: ')+rollout.error:tr('等待选中动作的 Q 记录；旧回合不补值','Awaiting selected-action Q; no historical values inferred')});
      note.textContent=tr('Q 不是成功概率。接管成功不计为自主成功；放弃、未标注及 HIL 信息缺失的回合不进入成功率分母。曲线为原始采样，暂停或断连时不补点。','Q is not a success probability. Assisted successes are not autonomous successes; aborted, unlabeled or unknown-HIL episodes are excluded. Raw samples; no points added while paused or disconnected.')+(successes.excluded?tr(` 已排除 ${successes.excluded} 轮。`,` ${successes.excluded} episodes excluded.`):'');
      note.textContent+=tr(' 可选择已保存回合查看 Q 历史；上线前被覆盖的回合无法恢复。',' Select a saved episode to view Q history; overwritten records from before archiving cannot be recovered.');
      if(rlt)note.textContent+=tr(' 仅显示 Online 更新，Warmup 分开；单轮 Q1 使用本轮固定的已保存 Critic，在 CPU 后台评分，反映执行滤波前已采用的动作块；暂停、接管或丢失采样处断线。',' Online updates only; Warmup excluded. Episode Q1 uses a pinned saved Critic, asynchronously scored on CPU before execution filtering; pause, HIL and missing samples break the line.');
    }
    async function poll(){
      if(!selection||inflight||Date.now()-lastPoll<1900||document.hidden)return;
      lastPoll=Date.now();const key=selection+'|'+rolloutSelection,controller=new AbortController();inflight=controller;
      const timeout=setTimeout(()=>controller.abort(),5000);
      try{
        const params=new URLSearchParams({model_id:model.id});if(rolloutSelection)params.set('episode',rolloutSelection);if(options){params.set('rtc_delay',options.rtc_delay);params.set('edit_scale',options.edit_scale);}
        const response=await root.fetch('/api/collection/rl-monitoring?'+params,{cache:'no-store',signal:controller.signal});
        if(!response.ok)throw Error(String(response.status));const value=await response.json();
        if(selection+'|'+rolloutSelection!==key)return;payload=value;error='';render();
      }catch(e){if(selection+'|'+rolloutSelection===key){error=String(e);render();}}
      finally{clearTimeout(timeout);if(inflight===controller)inflight=null;}
    }
    function update(chosen,current,selectedOptions){
      model=chosen;state=current||{};const enabled=chosen?.runtime_adapter==='expo_online'||(chosen?.kind==='rlt'&&chosen?.mode==='online'&&chosen?.training_enabled===true);supported=Boolean(enabled);section.hidden=false;
      options=chosen&&state.model?.id===chosen.id&&state.phase!=='offline'?(state.model.execution_settings?.expo||chosen?.expo_online_options?.defaults):(selectedOptions?.expo||chosen?.expo_online_options?.defaults);
      if(chosen?.kind==='rlt')options=null;
      nativeMetrics?.update(chosen,options);
      const key=enabled?JSON.stringify([chosen.id,options]):'';
      if(key!==selection){selection=key;rolloutSelection='';payload=null;error='';lastPoll=0;inflight?.abort();inflight=null;render();}
      if(enabled)poll();else render();
    }
    timer=setInterval(poll,2000);document.addEventListener('cobot:language',render);
    return {update,render,destroy(){nativeMetrics?.destroy();clearInterval(timer);inflight?.abort();document.removeEventListener('cobot:language',render);section.remove();}};
  }
  root.CobotRLMonitoring={create,chart};
  if(typeof module!=='undefined'&&module.exports)module.exports=root.CobotRLMonitoring;
})(typeof window!=='undefined'?window:globalThis);
