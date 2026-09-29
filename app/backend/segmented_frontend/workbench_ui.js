"use strict";
(function(root){
  const $ = selector => document.querySelector(selector);
  function facts(node, rows) {
    node.replaceChildren();
    for (const [key, value] of rows) {
      const label = document.createElement("dt"); label.textContent = key;
      const detail = document.createElement("dd"); detail.textContent = value == null ? "—" : String(value);
      node.append(label, detail);
    }
  }
  function selectedModel() { return catalog.find(model => model.id === $("#deployment-model").value); }
  function renderTraining() {
    const family=$("#training-family").value,run=$("#training-run").value;
    const dagger=family==="dagger",stage1=run==="plug_v3_stage1";
    const released=run==="plug_v3_warmup_experts120_5k";
    const comparison=run==="plug_v3_warmup_experts120_20k";
    $("#training-figures").classList.toggle("hidden",dagger||stage1);
    const gate=$("#training-gate");
    gate.textContent=dagger?"历史归档":stage1?"Stage 1 · 已离线验证":released?"Warmup 5k · 真机待验收":comparison?"120 专家 20k · 离线对照":"旧 Warmup 20k · 未通过";
    gate.dataset.tone=dagger?"gray":stage1?"green":released?"amber":"red";
    facts($("#training-facts"),dagger?[
      ["来源","cobot-platform/archive/xiaomi-robotics-1-dagger-round001"],
      ["状态","历史部署记录，未纳入 plug_v3 RLT 训练"],
      ["W&B","当前未登记 run 链接"],
    ]:stage1?[
      ["模型","plug_v3 Stage 1 · step_4999"],["专家数据","134 条，单右臂 7D"],
      ["来源","Yyshadow/openpi-RLT Stage 1"],["状态","Cobot 零 publisher 离线验证通过"],
      ["W&B","当前未登记 run 链接"],
    ]:released?[
      ["训练","5,000 updates · batch 128 · seed 42"],["训练数据","84 rollout + 120 成功专家 / 2,567 transitions"],
      ["独立留出","20 rollout / 370 transitions"],["BC / Q 权重","10 / 0.1"],
      ["早 / 中 / 晚 Q AUC","0.626 / 0.657 / 1.000"],["整段 Q AUC","0.889"],
      ["HIL 六关节 MAE","Actor 0.001963 rad；Reference 0.001981 rad"],
      ["在线起点","learner 5,000 · actor 2,500；无动作接续测试通过"],
    ]:comparison?[
      ["训练","20,000 updates · batch 128 · seed 42"],["训练数据","同一 2,567 transitions"],
      ["独立留出","同一 20 rollout / 370 transitions"],["BC / Q 权重","10 / 0.1"],
      ["早 / 中 / 晚 Q AUC","0.414 / 0.434 / 0.970"],["整段 Q AUC","0.717"],
      ["HIL 六关节 MAE","Actor 0.001988 rad；Reference 0.001981 rad"],
      ["状态","离线对照；完整权重已备份，当前不部署"],
    ]:[
      ["训练","20,000 updates · batch 128 · seed 42"],["训练数据","84 episodes / 1,381 transitions"],
      ["完整留出","20 episodes / 370 transitions"],["BC / Q 权重","10 / 0.1"],
      ["早 / 中 / 晚 Q AUC","0.364 / 0.253 / 0.232"],
      ["HIL 六关节 MAE","Actor 0.002005 rad；Reference 0.001981 rad"],
      ["W&B","当前运行无登记 run 链接；以下为真实离线图"],
    ]);
    $("#training-note").textContent=dagger?"此批属于 Xiaomi XR-1/DAgger 历史归档；不要与本轮 RLT replay 混用。":stage1
      ? "这是 reference 基线；训练和真机在线学习是两个独立阶段。"
      : released?"5k 在同一留出集的 Q 排序和 HIL 拟合优于 20k；真实顺滑、成功率和持续在线改善仍待现场验证。"
      : comparison?"20k 动作步长更小，但留出 Q 排序及 HIL 拟合弱于 5k；仅保留作对照和回滚。"
      : "旧版仅用 rollout 训练，留出 Q 排序未通过；未部署。";
    const plots=released?[
      ["训练损失","/assets/plug_v3_warmup_20260925_5k/loss_curves.png"],
      ["留出集 Q 排序","/assets/plug_v3_warmup_20260925_5k/holdout_auc_by_stage.png"],
      ["Q 随 Episode 位置","/assets/plug_v3_warmup_20260925_5k/holdout_q_by_progress.png"],
      ["Actor 对 HIL 动作误差","/assets/plug_v3_warmup_20260925_5k/hil_action_error.png"],
      ["动作相邻步变化","/assets/plug_v3_warmup_20260925_5k/motion_step_p95.png"],
    ]:comparison?[
      ["训练损失","/assets/plug_v3_warmup_20260925/loss_curves.png"],
      ["留出集 Q 排序","/assets/plug_v3_warmup_20260925/holdout_auc_by_stage.png"],
      ["Q 随 Episode 位置","/assets/plug_v3_warmup_20260925/holdout_q_by_progress.png"],
      ["Actor 对 HIL 动作误差","/assets/plug_v3_warmup_20260925/hil_action_error.png"],
    ]:[
      ["离线 warmup 训练 loss","/assets/plug_v3_warmup_20260924/loss_curves.png"],
      ["训练与留出集 Q 排序","/assets/plug_v3_warmup_20260924/train_vs_holdout_q_auc.png"],
      ["留出集 Q 排序 AUC","/assets/plug_v3_warmup_20260924/holdout_auc_by_stage.png"],
      ["Q 随 Episode 位置","/assets/plug_v3_warmup_20260924/holdout_q_by_progress.png"],
      ["Actor 对 HIL 动作误差","/assets/plug_v3_warmup_20260924/holdout_hil_action_error.png"],
    ];
    $("#training-figures").querySelectorAll("figure").forEach((figure,index)=>{
      figure.classList.toggle("hidden",index>=plots.length);
      if(plots[index]){figure.querySelector("figcaption").textContent=plots[index][0];const img=figure.querySelector("img");img.alt=plots[index][0];img.loading="lazy";img.src=plots[index][1];}
    });
    const ckpt=$("#training-checkpoint").value;
    $("#training-command").textContent=ckpt==="warmup5k_experts"
      ? "Cobot: /media/agilex/Getea1/jiaan/projects/rlt/runs/plug_v3_yyshadow/online/checkpoints/latest.pkl\n入口: /media/agilex/Getea1/jiaan/projects/cobot-platform/scripts/rlt_v3_up.sh online"
      : ckpt==="warmup20k_experts"
      ? "A6000: /data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/plug_v3_yyshadow/warmup_20260925_trials/experts120_20000/checkpoints/latest.pkl\n离线对照；当前不部署。"
      : ckpt==="warmup20k"
      ? "A6000: /data/LFT-W02_data/jiaan/scratch/cobot-realworld-rl/runs/plug_v3_yyshadow/warmup_20260924_v1/offline_train_20k_v1/checkpoints/latest.pkl\n门禁未通过；仅供诊断，不能直接继续在线学习。"
      : "Cobot: /media/agilex/Getea1/jiaan/projects/rlt/deployments/plug_v3_yyshadow/manifest.json\nStage 1 checkpoint 由发布清单精确登记。";
    root.CobotFeaturePaths?.update("training",{run});
  }

  function mount(){
    $("#training-family").addEventListener("change",()=>{
      const dagger=$("#training-family").value==="dagger";
      $("#training-run").replaceChildren(...(dagger?[["dagger_archive","XR-1 DAgger 归档"]]:[["plug_v3_warmup_experts120_5k","plug_v3 · 120 专家 warmup 5k"],["plug_v3_warmup_experts120_20k","plug_v3 · 120 专家 warmup 20k 对照"],["plug_v3_warmup_20k","plug_v3 · 旧 warmup 20k"],["plug_v3_stage1","plug_v3 · Stage 1 reference"]]).map(([value,label])=>{const option=document.createElement("option");option.value=value;option.textContent=label;return option;}));
      renderTraining();
    });
    $("#training-run").addEventListener("change",()=>{
      const run=$("#training-run").value;
      $("#training-checkpoint").value=run==="plug_v3_warmup_experts120_5k"?"warmup5k_experts":run==="plug_v3_warmup_experts120_20k"?"warmup20k_experts":run==="plug_v3_warmup_20k"?"warmup20k":"stage1";
      renderTraining();
    });
    $("#training-checkpoint").addEventListener("change",renderTraining);
    $("#training-copy").addEventListener("click",async()=>{try{await root.navigator.clipboard.writeText($("#training-command").textContent);$("#training-copy-status").textContent="已复制";}catch(_error){$("#training-copy-status").textContent="请手动复制上方路径";}});

    renderTraining();
  }
  root.CobotWorkbenchUI={mount,updateModels:()=>{}};
  mount();
})(globalThis);
