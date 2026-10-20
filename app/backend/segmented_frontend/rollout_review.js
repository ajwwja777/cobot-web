/* Read-only recorded rollout review. Only explicit server time maps enable video/Q sync. */
(function(root){
 'use strict';
 const finite=v=>typeof v==='number'&&Number.isFinite(v);
 const tr=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
 const fmt=v=>finite(v)?Number(v.toFixed(4)).toString():'—';
 function validSegments(alignment){
  if(alignment?.status!=='aligned'||!alignment.basis||!Array.isArray(alignment.segments))return [];
  const segments=alignment.segments;
  let previous=null;
  for(const s of segments){
   if(!['video_start','video_end','episode_start','episode_end'].every(k=>finite(s[k]))||s.video_start<0||s.episode_start<0||s.video_end<=s.video_start||s.episode_end<=s.episode_start)return [];
   if(previous&&(s.video_start<previous.video_end||s.episode_start<previous.episode_end))return [];
   previous=s;
  }
  return segments;
 }
 function mapTime(value,segments,from,to){
  if(!finite(value))return null;
  const s=segments.find(p=>value>=p[from+'_start']&&value<=p[from+'_end']);
  return s?s[to+'_start']+(value-s[from+'_start'])/(s[from+'_end']-s[from+'_start'])*(s[to+'_end']-s[to+'_start']):null;
 }
 function safeVideoURL(value){
  if(typeof value!=='string'||!value)return '';
  try{const url=new URL(value,root.location.href);return url.origin===root.location.origin&&url.pathname.startsWith('/api/')?url.pathname+url.search:'';}catch{return '';}
 }
 function create(card,plot){
  const facts=document.createElement('p');facts.className='rl-rollout-facts';
  const versionNote=document.createElement('p');versionNote.className='rl-policy-version-note';versionNote.hidden=true;
  const layout=document.createElement('div');layout.className='rl-rollout-review';
  const media=document.createElement('div');media.className='rl-rollout-media';
  const video=document.createElement('video');video.controls=true;video.preload='metadata';video.playsInline=true;video.setAttribute('aria-label','历史回合录像 / Recorded episode');
  const videoEmpty=document.createElement('p');videoEmpty.className='rl-chart-empty';
  media.append(video,videoEmpty);
  const qPanel=document.createElement('div');qPanel.className='rl-rollout-q';
  const readout=document.createElement('p');readout.className='rl-rollout-readout';readout.setAttribute('aria-live','off');
  qPanel.append(plot,readout);layout.append(media,qPanel);
  const alignmentNote=document.createElement('p');alignmentNote.className='rl-alignment-note';
  card.append(facts,versionNote,layout,alignmentNote);
  let current={},segments=[],chartHandle=null,identity='',disposed=false;
  function updateCursor(){
   const time=mapTime(video.currentTime,segments,'video','episode');
   chartHandle?.setCursor(time);
   if(time===null){readout.textContent=tr('录像与 Q 待对齐；不显示同步游标','Video/Q alignment unavailable; synchronized cursor disabled');return;}
   const points=(current.points||[]).filter(p=>finite(p.seconds)&&p.seconds<=time);
   const point=points.at(-1);
   readout.textContent=tr('回合时间 ','Episode time ')+fmt(time)+tr(' 秒 · 最近已记录 Q：',' s · Last recorded Q: ')+fmt(point?.q)+(point?tr('（采样时刻 ',' (sample at ')+fmt(point.seconds)+' s)':'')+(finite(point?.actor_version)?` · Actor ${point.actor_version}`:'')+(finite(point?.precache_actor_version)&&point.precache_actor_version!==point.actor_version?tr(` · 预计算 Actor ${point.precache_actor_version}`,` · Precache Actor ${point.precache_actor_version}`):'');
  }
  function seek(time){
   const target=mapTime(time,segments,'episode','video');
   if(target===null)return;
   video.currentTime=target;updateCursor();
  }
  video.addEventListener('timeupdate',updateCursor);video.addEventListener('seeking',updateCursor);
  video.addEventListener('error',()=>{videoEmpty.hidden=false;videoEmpty.textContent=tr('录像暂不可用，Q 历史仍保留','Video unavailable; Q history retained');});
  return {
   update(rollout,{rlt=false}={}){
    if(disposed)return;current=rollout||{};const review=current.review||{};
    const url=safeVideoURL(review.video_url),nextIdentity=String(current.episode_uuid||'')+'|'+url;
    segments=url?validSegments(review.alignment):[];
    if(nextIdentity!==identity){identity=nextIdentity;if(video.getAttribute('src')){video.pause();video.removeAttribute('src');}if(url)video.setAttribute('src',url);video.currentTime=0;}
    video.hidden=!url;videoEmpty.hidden=Boolean(url);videoEmpty.textContent=current.active?tr('本回合录制中，结束后可回放','Recording in progress; playback available after completion'):tr('该回合暂无可关联录像','No linked recording for this episode');
    const result={success:tr('成功','Success'),failure:tr('失败','Failure'),aborted:tr('放弃','Aborted')};
    const o=review.options||{},items=[tr('结果：','Outcome: ')+(result[review.outcome]||tr('未标注','Unlabeled')),tr('时长：','Duration: ')+fmt(review.duration_seconds)+' s',tr('接管：','Assistance: ')+(review.hil===true?tr('有','yes'):review.hil===false?tr('无','no'):tr('未知','unknown'))];
    if(!rlt)items.push('d '+(o.rtc_delay??'—'),'Edit '+fmt(o.edit_scale));
    facts.textContent=items.join(' · ');
    const provenance=root.CobotRLMonitoring.policyProvenance(current);versionNote.hidden=provenance.versions.length<=1;versionNote.textContent=versionNote.hidden?'':tr('本回合使用了多个模型版本：Q 的变化也可能来自权重变化，不能只解释为任务状态变化。','Multiple policy versions were used in this episode: Q changes may reflect weight updates as well as task-state changes.');
    const series=[{key:'q',label:tr('所选动作块 Q','Selected action chunk Q'),color:'#ef94be'}];
    if(!rlt&&(current.points||[]).some(p=>finite(p.best_unedited_q)))series.push({key:'best_unedited_q',label:tr('最佳未编辑候选 Q','Best unedited candidate Q'),color:'#f2bf66',dash:'5 4'});
    const rows=[];
    for(const point of current.points||[]){
     const previous=rows.at(-1);
     if(previous&&finite(point.seconds)&&finite(previous.seconds)&&segments.some((s,i)=>{const next=segments[i+1];return next&&next.episode_start-s.episode_end>1e-6&&previous.seconds<next.episode_start&&point.seconds>s.episode_end;}))rows.push({seconds:null,q:null});
     rows.push(point);
    }
    chartHandle=root.CobotRLMonitoring.chart(plot,rows,'seconds',series,{integerX:false,xlabel:tr('本回合开始后的时间（秒）','Seconds since episode start'),onSeek:url&&segments.length?seek:null,empty:current.error?tr('Q 评分暂不可用：','Q unavailable: ')+current.error:tr('等待动作块 Q 记录；未记录的历史不补值','Awaiting action-chunk Q; no historical values inferred')});
    alignmentNote.textContent=url&&segments.length?tr('粉线：所选动作块 Q；黄虚线：最佳未编辑候选 Q（有记录时）。拖动录像或点击曲线定位；按实际时间映射同步，暂停间隙不补点。','Pink: selected chunk Q; dashed yellow: best unedited Q when recorded. Scrub video or click the chart. Explicit time mapping; no samples inferred during pauses.'):
      tr('录像与 Q 待对齐：缺少明确时间映射时可分别查看，不把两个时间轴直接等同。','Video/Q alignment pending: view separately until an explicit time map is available.');
    updateCursor();
   },destroy(){disposed=true;video.removeEventListener('timeupdate',updateCursor);video.removeEventListener('seeking',updateCursor);if(video.getAttribute('src'))video.pause();layout.remove();facts.remove();versionNote.remove();alignmentNote.remove();}
  };
 }
 root.CobotRolloutReview={create,validSegments,mapTime,safeVideoURL};
})(window);
