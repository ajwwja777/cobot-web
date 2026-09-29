"use strict";
(function(root){
 const en=()=>document.documentElement.lang.startsWith("en"),t=(zh,eng)=>en()?eng:zh;
 const $=id=>document.getElementById(id),num=v=>v==null?"—":Number(v).toLocaleString("en-US",{maximumFractionDigits:5});
 const pct=v=>v==null?"—":(100*v).toFixed(1)+"%";
 let data=null,scope="transitions",dimension="outcome",batch="",visualFrame="0",heat=true;
 const labels={outcome:["轮次结局","Episode outcome"],phase:["Warmup / Online","Warmup / Online"],age:["旧 / 新数据","Data age"],
  portion:["头 / 中 / 尾","Early / middle / late"],source:["控制来源","Control source"],hil:["当前 chunk 含人工动作","Human actions in chunk"],episode_hil:["整轮含人工动作","Human actions in episode"]};
 function el(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e;}
 function panel(id,title,wide=true){const p=el("article",null,"analysis-card"+(wide?" analysis-wide":""));p.id=id;const h=el("header");h.append(el("h3",title));p.append(h,el("div",null,"analysis-card-body"));return p;}
 function table(parent,headers,rows){parent.replaceChildren();const wrap=el("div",null,"analysis-table-wrap"),tb=el("table",null,"analysis-table");
  const head=el("thead"),tr=el("tr");headers.forEach(x=>tr.append(el("th",x)));head.append(tr);tb.append(head);
  const body=el("tbody");rows.forEach(row=>{const tr=el("tr");row.forEach(x=>{const td=el("td");if(x instanceof root.HTMLElement)td.append(x);else td.textContent=x;tr.append(td);});body.append(tr);});tb.append(body);wrap.append(tb);parent.append(wrap);}
 function bar(ratio){const track=el("div",null,"audit-ratio-bar"),fill=el("i");fill.style.width=Math.max(0,Math.min(100,100*ratio))+"%";track.append(fill);return track;}
 function select(id,items,value){const e=$(id);e.replaceChildren(...items.map(([v,label])=>{const o=el("option",label);o.value=v;return o;}));e.value=value;}
 function mount(grid){
  const comp=panel("audit-composition","Replay / batch");
  const body=comp.lastChild,bar=el("div",null,"analysis-toolbar");
  for(const [id,change] of [["audit-scope",v=>scope=v],["audit-dimension",v=>dimension=v],["audit-batch",v=>batch=v]]){
   const select=el("select");select.id=id;select.setAttribute("aria-label",id);select.addEventListener("change",()=>{change(select.value);render(data);});bar.append(select);
  }
  body.append(bar);for(const id of ["audit-summary","audit-warning","audit-table","audit-definitions"]){const e=el(id.includes("table")?"div":"p",null,"analysis-note");e.id=id;body.append(e);}
  const evalPanel=panel("audit-validation","Validation / sampling experiment");
  for(const id of ["audit-validation-note","audit-version-table","audit-experiment-table","audit-experiment-note"]){const e=el(id.includes("table")?"div":"p",null,"analysis-note");e.id=id;evalPanel.lastChild.append(e);}
  const sensitivity=panel("audit-influence","Input and loss sensitivity");
  for(const id of ["audit-sensitivity-note","audit-influence-table","audit-input-table","audit-q-probe"]){const e=el(id.includes("table")?"div":"p",null,"analysis-note");e.id=id;sensitivity.lastChild.append(e);}
  const visual=panel("audit-visual","Visual sensitivity");
  const controls=el("div",null,"analysis-toolbar"),frame=el("select"),toggle=el("input"),label=el("label");
  frame.id="audit-visual-frame";frame.setAttribute("aria-label","Recorded frame");frame.addEventListener("change",()=>{visualFrame=frame.value;renderVisual();});
  toggle.className="audit-overlay-toggle";label.className="audit-overlay-label";toggle.type="checkbox";toggle.checked=true;toggle.addEventListener("change",()=>{heat=toggle.checked;renderVisual();});
  label.append(toggle,el("span","Occlusion overlay"));controls.append(frame,label);visual.lastChild.append(controls);
  const note=el("p",null,"analysis-note");note.id="audit-visual-note";visual.lastChild.append(note);
  const images=el("div",null,"audit-camera-grid");images.id="audit-visual-images";visual.lastChild.append(images);
  grid.prepend(comp,evalPanel,sensitivity,visual);
  mountCredit(grid);
 }
 function render(d){data=d;if(!d||!$("audit-scope"))return;
  $("audit-composition").querySelector("h3").textContent=t("Replay 与实际训练 batch 构成","Replay and actual training batch composition");
  $("audit-validation").querySelector("h3").textContent=t("版本检查与采样对照实验","Version audit and sampling experiments");
  $("audit-influence").querySelector("h3").textContent=t("训练影响与输入敏感度","Training influence and input sensitivity");
  select("audit-scope",[["transitions","Replay · transitions"],["episodes","Replay · episodes"],["batch",t("实际 batch","Actual batch")]],scope);
  const validDimensions=scope==="episodes"?["outcome","phase","age","episode_hil"]:Object.keys(labels);
  if(!validDimensions.includes(dimension))dimension="outcome";
  select("audit-dimension",validDimensions.map(k=>[k,t(...labels[k])]),dimension);
  const batches=d.batches||[],choices=batches.map((b,i)=>[String(i),"Step "+b.global_step+" · "+b.run_id]);
  if(!choices.some(x=>x[0]===batch))batch=choices.at(-1)?.[0]||"";
  select("audit-batch",choices,batch);$("audit-batch").hidden=scope!=="batch";
  const rep=d.replay_composition||{},b=scope==="batch"?batches[Number(batch)]:rep[scope],rows=b?.dimensions?.[dimension]||[];
  $("audit-summary").textContent=scope==="batch"?t("实际抽样数：","Actual sampled transitions: ")+num(b?.count)+" · "+t("不重复 transition：","Unique transitions: ")+num(b?.unique_transitions):
   t("只读快照：","Read-only snapshot: ")+new Date((rep.generated_at||0)*1000).toLocaleString()+" · "+num(b?.count)+" "+scope;
  if(scope==="transitions"){
   const episodes=rep.episode_rows||[],online=episodes.filter(r=>r.phase==="online"),
    autonomous=online.filter(r=>r.outcome==="success"&&!r.episode_hil),
    n=autonomous.reduce((sum,r)=>sum+(r.transitions||0),0);
   $("audit-summary").textContent+=" · "+t("新自主成功：","New autonomous successes: ")+autonomous.length+"/"+online.length+
    t("轮；"," episodes; ")+n+"/"+num(rep.transitions?.count)+" transitions ("+pct(n/(rep.transitions?.count||1))+")";
  }
  $("audit-warning").textContent=scope==="batch"&&!b?t("旧日志没有逐 batch 身份，无法倒推成功/失败及头尾比例。新审计入口在下次启动 Learner 后记录每步；此处展示最近 128 个，完整记录保留在 batch_composition.jsonl。","Historical batch identities were not recorded, so episode outcome/position cannot be reconstructed. The new entry records every update on the next Learner launch. Latest 128 shown; full history is in batch_composition.jsonl."):
   t("同一维度可相加；不同维度重叠，不能相加。成功按完整轮次结局；含人工动作的 chunk 不等于整轮；Warmup 人工示范也计入 HUMAN，不全是在线接管。","Categories within one dimension sum; different dimensions overlap. Success means whole-episode outcome. Human-containing chunks and whole episodes differ. Warmup demonstrations also count as HUMAN; not all are online interventions.");
  table($("audit-table"),[t("类别","Category"),t("数量","Count"),t("比例","Ratio"),t("分布","Distribution")],
   rows.map(x=>[dimension==="source"?({"0":"BASE · Reference","1":"RL · Actor","2":"HUMAN","3":"MIXED"}[x.label]||x.label):x.label,num(x.count),pct(x.ratio),bar(x.ratio)]));
  $("audit-definitions").textContent=t("头中尾＝本轮已存窗口的顺序三等分，并非任务语义阶段。新＝最近 20 个 Online episode ID 窗口。抽样有放回；同一轮越长，在均匀 transition 抽样中权重越大。","Early/middle/late are rank thirds of stored windows, not task stages. Recent = a window of 20 Online episode IDs. Sampling is with replacement; longer episodes carry more weight in uniform transition sampling.");
  const diag=d.learning_diagnosis||{};
  $("audit-validation-note").textContent=t("已发布版本使用同一组 Online 轮次做回看；这些模型可能训练过它们，因此不是独立验证集，更不是成功率。下方采样实验从 Warmup 5000 出发，按整轮留出新的 Online 数据。","Published versions are audited on the same Online cohort, which they may have trained on: NOT independent validation or success rate. Sampling experiments start at Warmup 5000 and hold out complete new Online episodes.")+" n="+(diag.holdout_online_episodes?.length??"—");
  table($("audit-version-table"),["Learner","Actor",t("HIL MAE (rad)","HIL MAE (rad)"),"Reference MAE",t("动作偏移 RMS","Action shift RMS"),"Q AUC · early / middle / late"],
   (diag.versions||[]).slice().sort((a,b)=>a.learner_step-b.learner_step).map(v=>[num(v.learner_step),num(v.actor_version),num(v.human_mae_rad),num(v.reference_human_mae_rad),num(v.actor_reference_rms_rad),v.q_by_portion?["early","middle","late"].map(k=>num(v.q_by_portion[k]?.auc)).join(" / "):t("该发布步未保留 Critic","Critic not retained at this step")]));
  table($("audit-experiment-table"),[t("配方","Sampling"),"Seed",t("实际成功比例","Actual success share"),"HIL MAE","Q AUC · early / middle / late"],
   (diag.experiments||[]).map(e=>[e.variant,num(e.seed),pct(e.mean_sample_success),num(e.validation.human_mae_rad),["early","middle","late"].map(k=>num(e.validation.q_by_portion[k]?.auc)).join(" / ")]));
  $("audit-experiment-note").textContent=t("相同初始化、更新数、损失与动作归一化；只改变抽样比例。3 个种子、少量留出轮次只能筛查假设。AUC 衡量 Q 对轮次结局的排序，不证明它能选择更好的动作。不自动部署实验参数。","Same initialization, updates, losses and action normalization; only sampling changes. Three seeds and a small holdout screen hypotheses. Q outcome AUC does not establish action selection quality. Experimental parameters are never auto-deployed.")+" · "+num(diag.updates)+" updates";
  const s=d.sensitivity||{};
  $("audit-sensitivity-note").textContent=t("以下是离线梯度与输入消融，不是 Attention 热图。RLT 小型 Actor/Critic 是 MLP；缓存的 z_rl 无法还原视觉 patch 的注意力。训练项梯度方向才反映更新拉向何方，loss 数值大小不能代替。","These are offline gradients and input ablations, NOT attention maps. The small RLT Actor/Critic are MLPs; cached z_rl cannot recover visual patch attention. Gradient direction describes local update pressure; raw loss magnitudes do not.")+" "+(s.checkpoint||"");
  table($("audit-influence-table"),[t("数据组","Data group"),"n","‖∇ BC‖","‖∇ −Q‖","‖∇ Δ‖","cos(BC, −Q)"],
   (s.gradients||[]).map(g=>[g.group,num(g.count),num(g.bc_norm),num(g.q_norm),num(g.delta_norm),num(g.bc_q_cosine)]));
  table($("audit-input-table"),[t("消融","Ablation"),t("动作变化 RMS (rad)","Action change RMS (rad)"),t("含义","Meaning")],
   (s.ablations||[]).map(a=>[a.name,num(a.action_shift_rms_rad),t("输入替换可能离分布；敏感性，不是因果解释","Input replacement may be out of distribution; sensitivity, not causal attribution")]));
  $("audit-q-probe").textContent=t("Q 对录制人工纠正方向的反应：这只是当前检查点的局部探针，负方向也可能离分布，不代表其动作更好。","Q response along recorded human corrections: a local checkpoint probe. The negative direction may be out of distribution; higher Q does not prove its action is better.");
  const probe=$("audit-q-probe"),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");svg.setAttribute("viewBox","0 0 300 120");svg.classList.add("audit-q-chart");
  probe.append(svg);
  root.CobotDiagnosticsUI?.renderChart(svg,(s.action_probes||[]).map(v=>({global_step:v.human_direction,q:v.q_mean})),[{key:"q",label:"Q1",color:"#e7b766"}],{xLabel:"Human correction alpha",xFormat:v=>Number(v).toFixed(2),xDigits:2,yLabel:"Q1",emptyText:"No probe"});
  probe.append(el("span",t("α=0 是 Actor，α=1 是所录 HIL 动作；不是成功率。","α=0: Actor; α=1: recorded HIL actions. This is not success rate.")));
  renderVisual();
  renderCredit();

 }
 function renderVisual(){
  if(!$("audit-visual-frame"))return;
  const v=data?.visual_sensitivity||{},frames=v.frames||[];
  if(!frames[Number(visualFrame)])visualFrame="0";
  select("audit-visual-frame",frames.map((f,i)=>[String(i),f.path.split("/").at(-1)+" · "+f.outcome+" · frame "+f.frame+"/"+f.total_frames]),visualFrame);
  $("audit-visual").querySelector("h3").textContent=t("相机与图像区域敏感度（离线）","Camera and image-region sensitivity (offline)");
  $("audit-visual-note").textContent=t("同一记录帧、相同状态和随机种子，逐块替换为该相机的均值颜色。红色越深，预测关节动作变化越大。三相机共用每帧色阶；这不是 Attention，也不能单凭颜色判断看对了哪里。","Same recorded frame, state and noise seed; replace each patch with the camera's mean color. Darker red means larger predicted joint-action change. Shared scale across cameras in each frame. This is NOT attention and does not by itself establish correct grounding.")+(frames.length?"":t(" · 正在等待离线结果"," · Offline result pending"));
  const parent=$("audit-visual-images");parent.replaceChildren();const f=frames[Number(visualFrame)];if(!f)return;
  const max=Math.max(1e-9,...f.cameras.flatMap(c=>c.patches.map(p=>p.action_shift_rad)));
  for(const c of f.cameras){
   const card=el("div",null,"audit-camera"),title=el("strong",c.camera),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
   svg.setAttribute("viewBox","0 0 320 240");svg.setAttribute("role","img");svg.setAttribute("aria-label",c.camera+" occlusion sensitivity");
   const image=document.createElementNS(svg.namespaceURI,"image");image.setAttribute("width","320");image.setAttribute("height","240");image.setAttribute("href","data:image/jpeg;base64,"+c.image_jpeg);svg.append(image);
   if(heat)for(const p of c.patches){const rect=document.createElementNS(svg.namespaceURI,"rect");rect.setAttribute("x",p.column*80);rect.setAttribute("y",p.row*60);rect.setAttribute("width","80");rect.setAttribute("height","60");rect.setAttribute("fill","#ff5349");rect.setAttribute("fill-opacity",.65*Math.sqrt(p.action_shift_rad/max));rect.setAttribute("stroke","rgba(255,255,255,.2)");const tip=document.createElementNS(svg.namespaceURI,"title");tip.textContent="RMS "+num(p.action_shift_rad)+" rad";rect.append(tip);svg.append(rect);}
   card.append(title,svg,el("small",t("整相机遮挡：","Whole camera: ")+num(c.full_camera_shift_rad)+" rad"));parent.append(card);
  }
  parent.append(el("p","0 — "+num(max)+" rad · "+f.path,"analysis-note"));
 }



 let creditVariant="baseline",creditEpisode="0";
 function mountCredit(grid){
  const card=panel("audit-credit","Credit assignment and action corrections");
  const toolbar=el("div",null,"analysis-toolbar");
  for(const [id,label] of [["audit-credit-variant","Experiment"],["audit-credit-episode","Episode window"]]){
   const s=el("select");s.id=id;s.setAttribute("aria-label",label);
   s.addEventListener("change",()=>{if(id.endsWith("variant")){creditVariant=s.value;creditEpisode="0";}else creditEpisode=s.value;renderCredit();});
   toolbar.append(s);
  }
  card.lastChild.append(toolbar);
  for(const id of ["audit-credit-note","audit-credit-table","audit-credit-summary","audit-credit-charts"]){
   const n=el(id.includes("note")||id.includes("summary")?"p":"div",null,"analysis-note");n.id=id;card.lastChild.append(n);
  }
  const allRuns=el("details"),detailsTitle=el("summary","All experiment runs");
  allRuns.append(detailsTitle,card.querySelector("#audit-credit-table"));
  card.lastChild.append(card.querySelector("#audit-credit-summary"),card.querySelector("#audit-credit-charts"),allRuns);
  grid.prepend(card);
 }
 function renderCredit(){
  if(!$("audit-credit-variant"))return;
  const report=data?.credit_assignment||{},runs=report.experiments||[];
  $("audit-credit").querySelector("h3").textContent=t("奖励传播实验与 Actor 动作修正","Credit experiments and Actor action corrections");
  $("audit-credit-note").textContent=t("同一 Warmup 初始化、相同更新数；按整轮排除开发集。此开发集已反复检查，不能当成独立测试集。中间 success=0 不代表没有 TD 信号；MC 是显式实验，不是重写原始奖励。","Same Warmup initialization and update count; development episodes excluded from training. This repeatedly examined cohort is not an independent test set. Intermediate success=0 does not remove TD credit; MC is an explicit experiment, not relabeled recorded rewards.")
   +" · "+(report.finished_at?t("已完成","Complete"):t("实验进行中或尚无结果","Running or no result yet"))+" · "+num(report.updates)+" updates";
  table($("audit-credit-table"),["Profile","Seed","HIL MAE (rad)","HIL improved","Correction cosine","Q AUC · early / middle / late","MC RMSE"],
   runs.map(r=>[r.variant,num(r.seed),num(r.validation.human_mae_rad),pct(r.validation.hil_steps_improved_ratio),num(r.validation.correction_human_cosine),
    ["early","middle","late"].map(k=>num(r.validation.q_by_portion[k]?.auc)).join(" / "),num(r.validation.mc_rmse)]));
  const views=[["warmup",report.baseline],...runs.filter(r=>r.seed===42).map(r=>[r.variant,r.validation])];
  if(!views.some(v=>v[0]===creditVariant))creditVariant=views[0]?.[0]||"warmup";
  select("audit-credit-variant",views.map(v=>[v[0],v[0]]),creditVariant);
  const view=views.find(v=>v[0]===creditVariant)?.[1],traces=view?.traces||[];
  if(!traces[Number(creditEpisode)])creditEpisode="0";
  select("audit-credit-episode",traces.map((r,i)=>[String(i),"Episode "+r.episode+" · step "+r.step+" · "+r.outcome]),creditEpisode);
  $("audit-credit-summary").textContent=t("只比较相同记录状态下的预测；绿色为人工/实际执行轨迹，不是自动成功轨迹。仅 HUMAN/MIXED 步参与改善比例和方向余弦；夹爪单独用米表示。","Predictions at the same recorded state. Green is human/recorded execution, not an autonomous successful trajectory. Improvement and direction cosine use HUMAN/MIXED steps only; gripper is shown separately in metres.");
  const parent=$("audit-credit-charts");parent.replaceChildren();parent.className="audit-action-grid";
  const legend=el("div",null,"analysis-toolbar");legend.style.gridColumn="1 / -1";
  for(const [label,color] of [["Reference","#8aa7ff"],["Actor","#e7b766"],["Executed / HIL","#64d8ad"]]){
   const item=el("span","\u2501 "+label);item.style.color=color;legend.append(item);
  }
  parent.append(legend);
  const trace=traces[Number(creditEpisode)];if(!trace)return;
  for(let joint=0;joint<7;joint++){
   const box=el("div"),svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
   svg.setAttribute("viewBox","0 0 300 120");svg.setAttribute("role","img");
   const label=joint===6?"Gripper (m)":"Joint "+(joint+1)+" (rad)";
   svg.setAttribute("aria-label",label+" reference actor executed");
   box.append(el("strong",label),svg);
   const series=trace.reference.map((a,i)=>({global_step:i,reference:a[joint],actor:trace.actor[i][joint],executed:trace.executed[i][joint]}));
   root.CobotDiagnosticsUI?.renderChart(svg,series,[{key:"reference",label:"Reference",color:"#8aa7ff"},{key:"actor",label:"Actor",color:"#e7b766"},{key:"executed",label:"Executed / HIL",color:"#64d8ad"}],
    {xLabel:"Logical step (20 Hz)",yLabel:joint===6?"m":"rad",yFormat:v=>Number(v).toFixed(4),emptyText:"No actions"});
   parent.append(box);
  }
 }
 root.CobotReplayAuditUI={mount,render};
})(window);
