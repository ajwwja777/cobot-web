"use strict";
(function(root){
 const $=s=>document.querySelector(s), en=()=>document.documentElement.lang.startsWith("en"), t=(zh,eng)=>en()?eng:zh;
 const esc=s=>String(s??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 const n=v=>v==null?"—":Number(v).toLocaleString(en()?"en-US":"zh-CN",{maximumFractionDigits:5});
 const pct=v=>v==null?"—":(v*100).toFixed(1)+"%", date=v=>v?new Date(v*1000).toLocaleString(en()?"en-US":"zh-CN"):"—";
 const colors=["#52cbb3","#7aafff","#e7b766","#c598e9","#ec8c8c","#90b8c2"];
 let data=null,busy=false,run=-1,cohort="",episode="",range=2000,allVersions=false;
 function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
 function text(zh,eng){return '<span data-zh="'+esc(zh)+'" data-en="'+esc(eng)+'">'+esc(t(zh,eng))+'</span>';}
 function note(zh,eng){return '<p class="analysis-note">'+text(zh,eng)+'</p>';}
 function card(id,zh,eng,body,wide){return '<article class="analysis-card'+(wide?' analysis-wide':'')+'"><header><h3>'+text(zh,eng)+'</h3></header><div class="analysis-card-body" id="'+id+'">'+body+'</div></article>';}
 function plot(id){return '<div class="analysis-chart"><svg id="'+id+'" viewBox="0 0 300 120" role="img"></svg></div><div class="analysis-legend" id="'+id+'-legend"></div>';}
 function tile(zh,eng,value,sub){return '<div class="analysis-stat"><span>'+text(zh,eng)+'</span><strong>'+esc(value)+'</strong><small>'+esc(sub||"")+'</small></div>';}
 function facts(id,values){const e=$(id);e.replaceChildren();for(const [k,v] of values)e.append(el("dt","",k),el("dd","",v??"—"));}
 function defs(keys){return keys.map((x,i)=>({key:x[0],label:x[1],color:colors[i]}));}
 function chart(id,rows,keys,opts){const definitions=defs(keys);$("#"+id).setAttribute("aria-label",keys.map(x=>x[1]).join(", "));
  root.CobotDiagnosticsUI.renderChart($("#"+id),rows,definitions,Object.assign({xLabel:"Learner step",emptyText:t("暂无已记录指标","No recorded metrics")},opts));
  root.CobotDiagnosticsUI.renderLegend($("#"+id+"-legend"),definitions);
 }
 function select(e,items,value){const signature=JSON.stringify(items);if(e.dataset.signature!==signature){e.replaceChildren(...items.map(([v,name])=>{const o=el("option","",name);o.value=v;return o;}));e.dataset.signature=signature;}e.value=value;}
 function heading(zh,eng,descZh,descEn){return '<div class="analysis-heading"><div><p class="analysis-eyebrow">RLT · plug_insertion</p><h2>'+text(zh,eng)+'</h2><p>'+text(descZh,descEn)+'</p></div><button class="secondary analysis-refresh" type="button">'+text("刷新指标","Refresh metrics")+'</button></div>';}
 function mount(){
  const training=$('[data-page="training"]'),analysis=$("#analysis-root");if(!training||!analysis)return;
  const archive=$("#training-run")?.closest("article"),figures=$("#training-figures");
  const old=el("div","analysis-compatibility");old.setAttribute("aria-hidden","true");
  while(training.firstChild)old.append(training.firstChild);training.append(old);
  const main=el("div","analytics-workspace");training.append(main);
  main.innerHTML=heading("训练","Training","训练进度与核心损失；数据、效果及聚类见诊断分析。","Training progress and core losses. Data, outcomes and clusters are in Analysis.")+
   '<p class="analysis-status" id="training-analysis-status" role="status"></p><div class="analysis-stats" id="training-stats"></div><div class="analysis-grid">'+
   card("training-runtime","运行与发布","Runtime & publication",'<dl class="analysis-facts" id="analysis-runtime-facts"></dl>')+
   card("training-config","在线训练配置","Online training configuration",'<dl class="analysis-facts" id="analysis-config-facts"></dl>'+note("读取登记的 online_rl.yaml；运行时覆盖未必包含在此。","Reads registered online_rl.yaml; runtime overrides may not be reflected."))+
   card("training-critic-panel","Critic 损失","Critic loss",plot("analysis-critic"))+
   card("training-actor-panel","Actor 目标","Actor objective",plot("analysis-actor")+note("只绘制实际更新 Actor 的步，排除 Critic-only 步的占位 0。","Only actual Actor updates; Critic-only zero placeholders are excluded."))+'</div>';
  analysis.className="analytics-workspace";
  analysis.innerHTML=heading("诊断分析","Analysis","从真实训练日志与 Replay 快照定位问题。","Inspect real training telemetry and a read-only Replay snapshot.")+
   '<p class="analysis-status" id="analysis-status" role="status"></p><div class="analysis-toolbar"><label>'+text("训练日志段","Training segment")+'<select id="analysis-run"></select></label><label>'+text("曲线范围","Chart window")+'<select id="analysis-range"><option value="500">500 steps</option><option value="2000" selected>2,000 steps</option><option value="0">All steps</option></select></label><label>'+text("轮次类型","Episode cohort")+'<select id="analysis-cohort"></select></label></div><div class="analysis-stats" id="analysis-stats"></div><div class="analysis-grid">'+
   card("analysis-outcomes","不同 Actor 的表现","Outcomes by Actor",'<label class="analysis-check"><input id="analysis-all-versions" type="checkbox"/>'+text("全部版本（默认最近 8 个）","All versions (latest 8 by default)")+'</label><div class="analysis-table-wrap"><table class="analysis-table" id="analysis-outcome-table"></table></div>'+note("仅统计写入 Replay 的已标注轮次。自主成功 / 总轮次；接管成功另列。区间为 95% Wilson；小样本和在线探索不能当成标准评测结论。","Only labeled episodes committed to Replay. Autonomous successes / all attempts; assisted successes are separate. Intervals: 95% Wilson. Small samples and online exploration are not a controlled evaluation."),true)+
   card("analysis-sampling-panel","实际抽样比例","Observed batch composition",plot("analysis-sampling")+note("Warmup、最近 Online、HIL 是有重叠的标签，不能相加。比例来自真实 batch 日志。","Warmup, recent Online and HIL overlap; do not sum them. Ratios come from actual batches."))+
   card("analysis-source-panel","最近 batch 来源","Recent batch sources",'<div id="analysis-source-bars"></div><p class="analysis-note" id="analysis-source-note"></p>')+
   card("analysis-q-panel","Q 与目标 Q","Q and target Q",plot("analysis-q")+note("Q 接近目标不等于任务成功；结合版本表现和留出集判断。","Matching target Q does not establish task success; compare outcomes and held-out evidence."))+
   card("analysis-objective-panel","Actor 各项加权贡献","Weighted Actor terms",plot("analysis-terms")+note("显示日志中的加权数值；目标中的正负号以算法实现为准。","Weighted values as logged; objective signs follow the algorithm implementation."))+
   card("analysis-map-panel","Replay 状态分布","Replay state distribution",'<div class="analysis-map-toolbar"><label>'+text("着色","Color")+'<select id="analysis-color"><option value="phase">Warmup / Online</option><option value="cluster">Cluster</option><option value="intervention">HIL</option></select></label><span id="analysis-map-meta"></span></div><svg id="analysis-map" viewBox="0 0 600 330" role="img" aria-label="Replay PCA"></svg><div class="analysis-legend" id="analysis-map-legend"></div><p class="analysis-note" id="analysis-map-note"></p><div id="analysis-clusters"></div>',true)+
   card("analysis-episode-panel","单轮追溯","Episode trace",'<label>'+text("选择 Replay 轮次","Select Replay episode")+'<select id="analysis-episode"></select></label><dl class="analysis-facts" id="analysis-episode-facts"></dl>')+
   card("analysis-chunk-panel","动作与 Reference","Actions and Reference",'<p class="analysis-note" id="analysis-chunk-note"></p>'+plot("analysis-chunk")+note("同一 Replay episode_id 的代表 chunk；曲线为六关节向量 RMS（rad），不混入夹爪。无相机帧映射时不猜测视频位置。","Representative chunk with the same Replay episode_id. Six-joint vector RMS (rad), excluding gripper. No video position is inferred without a frame mapping."))+
   card("analysis-provenance","来源与限制","Sources & limits",'<div id="analysis-sources"></div>'+note("聚类使用 7 维 proprio 与 7 维平均动作差，标准化后做 PCA；k-means 在原 14 维空间计算。它描述姿态和动作，不代表视觉阶段，也不证明某一簇导致失败。","Clustering uses 7D proprio and 7D mean action differences, standardized before PCA. K-means uses the original 14D space. This describes posture/action, not visual stages or failure causation.")+'<details><summary>'+text("重新生成分布快照（终端）","Rebuild distribution snapshot (terminal)")+'</summary><pre>cd /home/agilex/jiaan/project/rl-platform\nenvs/online/bin/python scripts/analyze_replay.py</pre>'+note("只读已登记的可信 Replay；不会开始推理、改变池或加载权重。","Reads only the registered trusted Replay; no inference, pool changes or weight loading.")+'</details>',true)+
   '</div><details class="analysis-archive"><summary>'+text("历史 Warmup 留出集与离线图","Historical Warmup hold-out results & figures")+'</summary><div id="analysis-archive-content"></div></details>';
  if(archive)$("#analysis-archive-content").append(archive);if(figures)$("#analysis-archive-content").append(figures);
  document.querySelectorAll(".analysis-refresh").forEach(b=>b.addEventListener("click",refresh));
  $("#analysis-run").addEventListener("change",e=>{run=Number(e.target.value);refresh();});
  $("#analysis-range").addEventListener("change",e=>{range=Number(e.target.value);render();});
  $("#analysis-cohort").addEventListener("change",e=>{cohort=e.target.value;render();});
  $("#analysis-all-versions").addEventListener("change",e=>{allVersions=e.target.checked;render();});
  $("#analysis-color").addEventListener("change",renderMap);
  $("#analysis-episode").addEventListener("change",e=>{episode=e.target.value;renderEpisode();});
  document.addEventListener("cobot:language",()=>{if(data)render();});
  const observer=new MutationObserver(()=>{if(active()){if(document.querySelector('[data-page="training"].active'))run=-1;refresh();}});
  for(const page of [training,analysis.closest(".page")])observer.observe(page,{attributes:true,attributeFilter:["class"]});
  root.setInterval(()=>{if(active()&&!document.hidden)refresh();},10000);if(active())refresh();
 }
 function active(){return !!document.querySelector('[data-page="training"].active,[data-page="analysis"].active');}
 async function refresh(){
  if(busy)return;busy=true;
  try{const response=await fetch("/api/analysis/rlt?run="+run,{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);data=await response.json();render();}
  catch(e){for(const id of ["#analysis-status","#training-analysis-status"]){const item=$(id);if(item){item.classList.add("is-stale");item.textContent=t("指标读取失败；保留上次结果：","Refresh failed; previous results retained: ")+e.message;}}}
  finally{busy=false;}
 }

 function render(){
  if(!data)return;const d=data,s=d.status||{},pub=d.publication||{},runs=d.runs||[],segment=runs.find(x=>x.id===d.selected_run),means=d.recent_means||{};
  const runtime=d.config?.runtime||{},rl=d.config?.experiment?.rl||{};
  const status=t("日志更新：","Telemetry: ")+date(s.timestamp)+" · "+(d.stale?t("历史快照 · 未收到新心跳","Historical snapshot · no recent heartbeat"):t("收到近期心跳","Recent heartbeat"))+" · "+t("读取于 ","Fetched ")+date(d.generated_at);
  for(const id of ["#analysis-status","#training-analysis-status"]){$(id).textContent=status;$(id).classList.toggle("is-stale",!!d.stale);}
  $("#training-stats").innerHTML=tile("Learner 已训练","Learner trained",n(s.global_step),"step")+tile("已发布 Actor","Published Actor",n(pub.published_actor_version),"Learner "+n(pub.published_learner_step))+tile("Replay","Replay",n(s.replay_size),"transitions")+tile("待更新预算","Pending updates",n(s.pending_update_budget),t("不是 Episode 数","Not an episode count"));
  facts("#analysis-runtime-facts",[[t("Learner 内部 Actor","Learner internal Actor"),n(s.actor_version)],[t("已发布 Learner 步","Published learner step"),n(pub.published_learner_step)],[t("发布时刻","Published at"),date(pub.published_at)],[t("日志训练 phase","Logged training phase"),s.ready_for_online?"Online":"Warmup / unknown"],[t("最近启动登记（非就绪状态）","Last launch record (not readiness)"),d.deployment_record?.model?.label||"—"],[t("登记的权重","Registered checkpoint"),d.deployment_record?.model?.checkpoint||"—"],[t("配置文件","Config file"),d.config_path]]);
  facts("#analysis-config-facts",[["Batch size",n(runtime.learner_service?.sample_batch_size)],["Updates / new transition",n(s.update_ratio??rl.grad_updates_per_cycle)],["Actor update period",n(rl.actor_update_period)],["Publish interval",n(runtime.learner_service?.push_actor_interval_steps)+" steps"],["BC / Q / delta",[rl.online_bc_weight,rl.online_q_weight,rl.delta_weight].map(n).join(" / ")],["Actor / Critic LR",[rl.actor_lr,rl.critic_lr].map(n).join(" / ")],["Gamma / target tau",[rl.gamma,rl.target_tau].map(n).join(" / ")]]);
  select($("#analysis-run"),[[-1,t("最新连续日志段","Latest contiguous segment")],...runs.map(x=>[x.id,"#"+(x.id+1)+" · "+n(x.start)+"–"+n(x.end)+" · "+date(x.timestamp)])],run);
  const cut=segment&&range?segment.end-range:-Infinity, rows=(d.series||[]).filter(x=>x.global_step>=cut),actors=(d.actor_series||[]).filter(x=>x.global_step>=cut);
  chart("analysis-critic",rows,[["critic_loss","Critic loss"]],{yLabel:"loss"});
  chart("analysis-actor",actors,[["actor_loss","Actor objective"]],{yLabel:"objective"});
  chart("analysis-sampling",rows,[["sample_warmup_demo_ratio","Warmup"],["sample_recent_online_ratio","Recent Online"],["sample_human_intervention_ratio","HIL"]],{yLabel:"ratio"});
  chart("analysis-q",rows,[["q1_mean","Q1"],["q2_mean","Q2"],["target_q_mean","Target Q"]],{yLabel:"Q"});
  chart("analysis-terms",actors,[["weighted_bc","Weighted BC"],["weighted_q","Weighted Q"],["weighted_delta","Weighted delta"]],{yLabel:"value"});
  const sources=[["sample_source_base_ratio","Reference"],["sample_source_rl_ratio","RL"],["sample_source_human_ratio","Human"],["sample_source_mixed_ratio","Mixed"]];
  $("#analysis-source-bars").replaceChildren(...sources.map(([key,title],i)=>{const row=el("div","analysis-source-row");row.append(el("span","",title));const track=el("div","analysis-track"),fill=el("i");fill.style.width=Math.max(0,Math.min(100,(means[key]||0)*100))+"%";fill.style.background=colors[i];track.append(fill);row.append(track,el("strong","",pct(means[key])));return row;}));
  $("#analysis-source-note").textContent=t("所选日志段末尾 ","Last ")+n(d.recent_count)+t(" 个 batch 的均值。每个 batch 的 128 是 transitions，不是完整 Episode。"," batches of the selected segment. Each batch of 128 contains transitions, not whole episodes.");
  const cohorts=[...new Set((d.episode_groups||[]).map(x=>x.cohort))];if(!cohorts.includes(cohort))cohort=cohorts.find(x=>x.startsWith("online"))||cohorts[0]||"";
  select($("#analysis-cohort"),cohorts.map(x=>[x,x]),cohort);
  const groups=(d.episode_groups||[]).filter(x=>x.cohort===cohort),eps=(d.episodes||[]).filter(x=>x.cohort===cohort);
  const totals=groups.reduce((a,g)=>({count:a.count+g.count,auto:a.auto+g.autonomous_successes,hil:a.hil+g.hil_count}),{count:0,auto:0,hil:0});
  $("#analysis-stats").innerHTML=tile("有效标注轮次","Committed episodes",n(totals.count),cohort)+tile("自主成功","Autonomous success",pct(totals.count?totals.auto/totals.count:null),n(totals.auto)+" / "+n(totals.count))+tile("出现接管","Episodes with HIL",pct(totals.count?totals.hil/totals.count:null),t("包含失败轮次","Includes failed episodes"))+tile("未计入轮次","Uncommitted episodes",n(d.excluded_uncommitted),t("写入 0 transitions 的日志记录","Log rows with 0 written transitions"));
  $("#analysis-outcome-table").innerHTML="<thead><tr>"+[t("Actor 版本","Actor version"),"n",t("自主成功","Autonomous success"),t("接管成功","Assisted success"),t("失败","Failure"),"95% CI"].map(x=>"<th>"+esc(x)+"</th>").join("")+"</tr></thead><tbody>"+(allVersions?groups:groups.slice(-8)).slice().reverse().map(g=>"<tr><td>"+esc(g.version)+'</td><td>'+g.count+'</td><td><div class="analysis-rate"><i style="width:'+g.autonomous_rate*100+'%"></i><span>'+g.autonomous_successes+" / "+g.count+" · "+pct(g.autonomous_rate)+'</span></div></td><td>'+g.assisted_successes+'</td><td>'+g.failures+'</td><td>'+g.autonomous_ci.map(pct).join(" – ")+"</td></tr>").join("")+"</tbody>";
  if(!eps.some(x=>x.key===episode))episode=eps.at(-1)?.key||"";
  select($("#analysis-episode"),eps.slice().reverse().map(x=>[x.key,"#"+x.episode_id+" · Actor "+x.version+" · "+(x.success?t("成功","Success"):t("失败","Failure"))+(x.assisted?" · HIL":"")+" · "+date(x.timestamp)]),episode);
  renderEpisode();renderMap();
  const nodes=(d.sources||[]).map(x=>{const row=el("div","analysis-source-file");row.append(el("code","",x.path),el("span","",x.error||[n(x.rows)+" rows",date(x.mtime),x.truncated?t("仅尾部 24 MiB","Tail 24 MiB only"):"",x.invalid_lines?t("忽略无效行 ","Ignored invalid lines ")+x.invalid_lines:""].filter(Boolean).join(" · ")));return row;});
  if(d.config_error)nodes.push(el("p","analysis-note",d.config_error));
  nodes.push(el("p","analysis-note",t("Step 回退或重复时拆分日志段。曲线最多 320 个抽样点，Actor 单独抽点；均值使用末尾 100 个原始 batch。轮次按 cohort 汇总已读取历史，版本之间未控制场景难度。","Step resets or duplicates split training segments. Charts show at most 320 sampled points; Actor is sampled separately; means use the last 100 raw batches. Episodes aggregate loaded history by cohort; scene difficulty is not controlled across versions.")));
  $("#analysis-sources").replaceChildren(...nodes);
 }
 function renderEpisode(){
  const e=data?.episodes?.find(x=>x.key===episode);
  facts("#analysis-episode-facts",e?[["Replay episode_id",e.episode_id],[t("完成时间","Completed"),date(e.timestamp)],["Actor start → end",n(e.actor_version_start)+" → "+n(e.actor_version_end)],[t("标注与接管","Outcome & intervention"),(e.success?"Success":"Failure")+" · HIL "+(e.assisted?"yes":"no")],["Transitions / dropped",n(e.transitions_written)+" / "+n(e.dropped_transitions)],["RL / base / human / mixed",[e.rl_steps,e.base_steps,e.human_steps,e.mixed_steps].map(n).join(" / ")],[t("耗时","Duration"),n(e.duration_sec)+" s"]]:[]);
  renderChunk(e?.episode_id);
 }
 function renderChunk(id){
  const chunk=data?.projection?.episode_chunks?.[String(id)];
  $("#analysis-chunk-note").textContent=chunk?("Replay #"+chunk.episode_id+" · step "+chunk.step_id+" · "+chunk.phase+" · source "+chunk.source):t("当前快照没有此轮的动作 chunk。","This snapshot has no action chunk for this episode.");
  const rms=a=>Math.sqrt(a.slice(0,6).reduce((sum,v)=>sum+v*v,0)/6);
  const rows=chunk?chunk.action.map((a,i)=>({frame:i,action:rms(a),reference:rms(chunk.reference[i]),delta:rms(a.map((v,j)=>v-chunk.reference[i][j]))})):[];
  chart("analysis-chunk",rows,[["action","Action RMS"],["reference","Reference RMS"],["delta","Difference RMS"]],{xKey:"frame",xLabel:"chunk index",yLabel:"rad"});
 }
 function renderMap(){
  const p=data?.projection||{},svg=$("#analysis-map");if(!svg)return;svg.replaceChildren();
  const add=(name,attrs,text,parent=svg)=>{const e=document.createElementNS("http://www.w3.org/2000/svg",name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text!=null)e.textContent=text;parent.append(e);return e;};
  if(!p.points?.length){add("text",{x:300,y:160,"text-anchor":"middle"},t("尚无分布快照；见下方生成命令","No snapshot yet; see the command below"));$("#analysis-map-meta").textContent="";$("#analysis-map-note").textContent="";return;}
  const mode=$("#analysis-color").value,xs=p.points.map(v=>v.x),ys=p.points.map(v=>v.y),minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const px=x=>48+(x-minX)/(maxX-minX||1)*530,py=y=>285-(y-minY)/(maxY-minY||1)*255;
  for(let i=0;i<5;i++){const x=48+530*i/4,y=285-255*i/4;add("line",{x1:48,y1:y,x2:578,y2:y,class:"chart-gridline"});add("text",{x,y:305,"text-anchor":"middle"},(minX+(maxX-minX)*i/4).toFixed(1));add("text",{x:40,y:y+4,"text-anchor":"end"},(minY+(maxY-minY)*i/4).toFixed(1));}
  add("text",{x:310,y:325,"text-anchor":"middle"},"PC1 · "+pct(p.explained_variance?.[0]));
  add("text",{x:16,y:155,transform:"rotate(-90 16 155)","text-anchor":"middle"},"PC2 · "+pct(p.explained_variance?.[1]));
  for(const point of p.points){
   const index=mode==="cluster"?point.cluster:mode==="intervention"?(point.intervention?1:0):(point.phase==="online"?1:0);
   const c=add("circle",{cx:px(point.x),cy:py(point.y),r:2.8,fill:colors[index%colors.length],opacity:.62,"data-episode":point.episode_id});
   add("title",{},"Replay #"+point.episode_id+" · step "+point.step_id+" · "+point.phase+" · cluster "+point.cluster,c);
   c.addEventListener("click",()=>{const e=data.episodes.find(x=>x.episode_id===point.episode_id);if(e){episode=e.key;cohort=e.cohort;render();}else{facts("#analysis-episode-facts",[["Replay episode_id",point.episode_id],[t("轮次日志","Episode telemetry"),t("未找到对应标注日志","No matching labeled rollout log")]]);}renderChunk(point.episode_id);$("#analysis-chunk-panel").scrollIntoView({block:"center",behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"});});
  }
  const legend=mode==="cluster"?(p.clusters||[]).map(x=>({key:String(x.cluster),label:"Cluster "+x.cluster,color:colors[x.cluster%colors.length]})):mode==="intervention"?defs([["n","No HIL"],["h","HIL"]]):defs([["w","Warmup / other"],["o","Online"]]);
  root.CobotDiagnosticsUI.renderLegend($("#analysis-map-legend"),legend);
  $("#analysis-map-meta").textContent=n(p.points.length)+" / "+n(p.transitions)+" transitions · "+date(p.generated_at);
  $("#analysis-map-note").textContent=t("快照不是实时池；两轴解释方差 ","Snapshot, not the live pool; two-axis explained variance ")+pct((p.explained_variance||[]).reduce((a,b)=>a+b,0))+t("。点击点查看该轮动作。",". Click a point to inspect that episode.")+(p.transitions!==data.status?.replay_size?t(" 快照数量不同，需要时在终端重新生成。"," Snapshot count differs; rebuild from the terminal when needed."):"");
  $("#analysis-clusters").innerHTML=(p.clusters||[]).map(x=>'<span class="analysis-cluster">C'+x.cluster+" · "+n(x.count)+" · Online "+pct(x.online/x.count)+" · HIL "+pct(x.hil/x.count)+"</span>").join("");
 }
 root.CobotAnalysisUI={mount,refresh};
 if(typeof document!=="undefined"){
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",mount,{once:true});
  else mount();
 }
})(globalThis);
