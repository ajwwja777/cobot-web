"use strict";
(function(root){
  const en=()=>root.CobotPreferences?.language==="en";
  const text=(zh,english)=>en()?english:zh;
  const advice={
    latest_episode_incomplete:["目录最后一轮尚未写完；保留原文件，等待写入结束或选择新的保存目录后再检查。","The latest recording is unfinished. Keep its files; wait for completion or select another destination, then check again."],
    latest_episode_invalid:["目录最后一轮数据无法验证；保留原文件，选择其他保存目录并检查。","The latest episode cannot be validated. Keep its files; select another destination and check again."],
    camera_stale:["相机画面未更新；检查相机任务和连接，画面恢复后再检查。","Camera frames are stale; check the camera task and connection, then check again."],
    handover_stale:["示教/控制权反馈未更新；检查机械臂节点和示教按钮。","Handover feedback is stale; check arm nodes and teach buttons."],
    streams_not_ready:["所列相机或关节流缺失、过期或格式异常；先检查对应设备。","Listed camera/joint streams are missing, stale or invalid; check those devices."],
    not_writable:["数据目录不可写；检查 Getea1 挂载和目录权限。","Destination is not writable; check the Getea1 mount and permissions."],
    disk_space_low:["数据盘空间不足；清理或选择有足够空间的目录。","Storage is low; free space or select a destination with enough space."],
    pending_episode_finalization:["仍有未完成的数据，请先处理当前轮次；不会自动丢弃。","An episode is still pending; finish it first. Recovery will not discard it."],
    recorder_worker_still_active:["写入线程仍在运行，不能强制重置；查看输出并等待完成。","The writer is still running; check output and wait. It will not be reset forcibly."],
    stop_session_before_recorder_recovery:["先结束 Session（保留模型），再恢复录制。","End the session (keep the model), then recover recording."],
    pause_policy_before_recorder_recovery:["先暂停/结束当前轮次，再检查恢复。","Pause/finish the current episode before recovery."]
  };
  function explanation(code){const pair=advice[code];return pair?pair[en()?1:0]:text("查看输出中列出的原因，修复对应设备或目录后重新检查。","Check the reported cause and fix the device or destination before checking again.");}
  function create(container){
    container.innerHTML='<summary></summary><p class="recorder-recovery-hint"></p><div class="button-row"><button type="button" class="recorder-check secondary"></button><button type="button" class="recorder-recover secondary"></button><button type="button" class="recorder-skip secondary"></button><button type="button" class="recorder-defer-file secondary"></button></div><p class="recorder-recovery-result" role="status"></p>';
    const hint=container.querySelector(".recorder-recovery-hint"),check=container.querySelector(".recorder-check"),recover=container.querySelector(".recorder-recover"),result=container.querySelector(".recorder-recovery-result");
    const skip=container.querySelector('.recorder-skip'),deferFile=container.querySelector('.recorder-defer-file');
    let state={},active=false,busy=false,last=null,lastError="",observedFault="",uncertain=false;
    function show(){
      container.hidden=Boolean(state.runtime_failure);
      const isRlt=state.model?.kind==='rlt';recover.hidden=skip.hidden=!isRlt;
      container.querySelector("summary").textContent=text("录制检查与恢复（保留模型）","Recording checks and recovery (keep model)");
      check.textContent=text("检查录制","Check recording");
      recover.textContent=text("恢复录制（保留模型）","Recover recording (keep model)");
      hint.textContent=state.session?.phase==="fault"
        ?text("录制 Session 异常，模型仍保留。检查原因后可恢复，不必释放权重。","Recording session needs recovery; weights stay loaded. Check the cause, then recover.")
        :text("录制遇到错误时先检查；恢复不会开始推理或释放模型。","Check here if recording fails. Recovery never starts inference or unloads the model.");
      if(state.session?.fault_reason||state.session?.terminal_reason)hint.textContent+=" "+(state.session.fault_reason||state.session.terminal_reason);
      skip.textContent=text('暂存并跳过本轮（保留任务）','Defer and skip episode (keep task)');
      deferFile.textContent=text('暂存阻塞文件，稍后处理','Defer blocking file for later review');
      const phase=state.session?.phase;
      skip.disabled=busy||uncertain||Boolean(state.operation)||!['rollout','hil','paused','terminal_pending','fault'].includes(phase)||(phase==='fault'&&!String(state.session?.fault_reason||'').startsWith('task5_'));
      deferFile.hidden=!last?.preflight?.blocker;
      deferFile.disabled=busy||uncertain||active||Boolean(state.operation);
      check.disabled=busy||Boolean(state.operation)||(active&&!uncertain);
      recover.disabled=check.disabled||uncertain||active||state.session?.policy_paused!==true||!["fault","stopped"].includes(state.session?.phase);
      if(busy){result.textContent=text("正在检查/恢复…","Checking/recovering…");return;}
      if(lastError){result.textContent=lastError;return;}
      if(!last){result.textContent="";return;}
      const p=last.preflight||last.readiness;
      if(p?.status==="ok"){
        result.textContent=last.model_retained
          ?text("已恢复，模型保留。点击“开始 Session”，再手动开始采集。","Recovered; model retained. Start the session, then start capture manually.")
          :last.preflight?text("录制预检通过；相机、关节反馈、目标目录和历史记录完整性已检查。","Recording preflight passed: cameras, joint feedback, destination and previous recording integrity checked."):text("实时输入已恢复；点击“检查录制”核对目标目录，或恢复 Session。","Live inputs are ready; check the destination or recover the session.");
      }else if(p){
        result.textContent=(p.error_code||"not_ready")+(p.detail||p.stale_keys?.length?" · "+(p.detail||p.stale_keys.join(", ")):"")+" — "+explanation(p.error_code);
      }
      if(last.preflight?.data_root)result.textContent+="\n"+last.preflight.data_root;
      if(last.preflight?.status==="ok"&&last.preflight.latest_labels_complete===false)result.textContent+="\n"+text("已有未标注记录已保留，可开始新一轮；不会自动标成功/失败或加入训练。","Existing unlabeled recordings are preserved; a new episode is allowed. They are not relabeled or added to training.");
      if(last.last_start_error&&!last.preflight)result.textContent+=" "+text("上次原因：","Last cause: ")+last.last_start_error;
    }
    async function parse(response){
      const p=await response.json();
      if(!response.ok){const detail=typeof p.detail==="string"?p.detail:JSON.stringify(p.detail||p);throw new Error(explanation(detail)+" ("+detail+")");}
      return p;
    }
    async function act(repair){
      if((repair?recover:check).disabled)return;
      busy=true;lastError="";show();
      try{
        if(uncertain&&!repair){
          await parse(await fetch('/api/rlt/session',{cache:'no-store'}));
          uncertain=false;await root.CobotCollectionModel?.refresh();await root.refreshConsole?.();return;
        }
        const data_root=root.document.querySelector("#collection-data-root")?.value.trim();
        last=await parse(await fetch(repair?"/api/rlt/recover-recorder":"/api/rlt/recorder-check",{
          method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({data_root,...(repair?{reset_fault_session:true}:{})})
        }));
        uncertain=false;
        await root.CobotCollectionModel?.refresh();
        await root.refreshConsole?.();
      }catch(error){lastError=error.message;}
      finally{busy=false;show();}
    }
    async function deferAction(file){
      if((file?deferFile:skip).disabled)return;
      if(!root.confirm(text('保留本轮文件为待处理，不标成功/失败、不写 Replay。只暂停并结束本轮，保留模型任务；随后手动开始新一轮。','Retain files for later review without success/failure labels or Replay insertion. Pause/end this episode, keep the model task, then start a new episode manually.')))return;
      busy=true;lastError='';show();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),30000);
      try{
        const body=file?{data_root:last.preflight.data_root,expected:last.preflight.blocker}:{episode_id:state.session.episode_id,generation:state.session.generation};
        const response=await fetch(file?'/api/rlt/defer-file':'/api/rlt/episode/skip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:controller.signal});
        last=await parse(response);lastError=text('本轮已暂存，模型任务保留；可手动开始下一轮。到历史补标签或删除。','Episode deferred; model task retained. Start the next episode manually. Label or delete retained recordings in history.');
        await root.CobotCollectionModel?.refresh();await root.refreshConsole?.();await root.refreshHistory?.({loadSelected:false});
      }catch(error){uncertain=controller.signal.aborted||error instanceof TypeError;lastError=uncertain?text('结果待确认：先检查状态，不重复跳过。','Result uncertain: check status before skipping again.'):error.message;}
      finally{clearTimeout(timer);busy=false;show();}
    }
    skip.addEventListener('click',()=>deferAction(false));deferFile.addEventListener('click',()=>deferAction(true));
    check.addEventListener("click",()=>act(false));recover.addEventListener("click",()=>act(true));
    return {update(next,isActive){
      state=next||{};active=Boolean(isActive);show();
      const fault=state.session?.phase==="fault"?String(state.session.generation)+":"+state.session.fault_reason:"";
      if(fault&&fault!==observedFault){observedFault=fault;last=null;lastError="";container.open=true;
        fetch("/api/rlt/recorder-diagnostics",{cache:"no-store"}).then(parse).then(value=>{last=value;show();}).catch(()=>{});
      }
    }};
  }
  function runtimeSummary(state){
    const fault=state?.runtime_failure;
    if(!fault)return "";
    const names={
      rtc_delay_exceeded:["RTC 执行超时","RTC execution timed out"],
      execution_clock_late:["控制发布超时","Control publication missed its deadline"],
      recorder_health_timeout:["录制状态检查超时","Recorder health check timed out"],
      observation_stale:["实时反馈过期","Live observations are stale"],
      gpu_out_of_memory:["GPU 显存不足","GPU memory exhausted"],
      execution_failed:["执行进程异常","Execution failed"],
      runtime_process_exited:["运行进程已退出","Runtime process exited"]
    };
    const pair=names[fault.code]||names.runtime_process_exited;
    return pair[en()?1:0]+(fault.stage1_retained
      ?text("；Stage1 权重仍保留","; Stage1 weights remain loaded")
      :text("；Stage1 未确认保留","; Stage1 retention is not confirmed"));
  }
  function createRuntime(container,{refresh}={}){
    container.classList.add("runtime-recovery");
    container.setAttribute("role","status");
    container.innerHTML='<strong class="runtime-fault-title"></strong><p class="runtime-fault-cause"></p><p class="runtime-fault-advice"></p><div class="button-row"><button type="button" class="runtime-check secondary"></button><button type="button" class="runtime-recover secondary"></button><button type="button" class="runtime-restart secondary"></button><button type="button" class="runtime-output secondary"></button></div><p class="runtime-recovery-result"></p>';
    const title=container.querySelector(".runtime-fault-title"),cause=container.querySelector(".runtime-fault-cause"),adviceNode=container.querySelector(".runtime-fault-advice");
    const check=container.querySelector(".runtime-check"),repair=container.querySelector(".runtime-recover"),restart=container.querySelector(".runtime-restart"),output=container.querySelector(".runtime-output"),result=container.querySelector(".runtime-recovery-result");
    let state={},busy=false,message="";
    function show(){
      const f=state.runtime_failure;
      container.hidden=!f&&(state.model?.kind!=="rlt"||state.phase==="offline");
      title.textContent=runtimeSummary(state)||text("运行恢复（保留模型）","Runtime recovery (keep model)");
      cause.textContent=f?.cause||"";
      const steps=text("恢复会结束当前轮次，将录制保留为未标注，不加入 Replay；重建运行进程后等待手动开始。不会释放 Stage1、归位或自动推理。","Recovery ends the current episode and retains its recording without a success/failure label or Replay insertion. The rebuilt runtime waits for manual start. Stage1 stays loaded; no homing or automatic inference.");
      adviceNode.textContent=(f?.code==="execution_clock_late"
        ?text("发布时钟超时；暂停/示教衔接已修复。若正常推理仍超时，查看 late_ms 和发布频率，改用同步 20 Hz。","Publication clock overrun; pause/HIL clock handling has been corrected. If active inference still overruns, inspect late_ms and publication Hz, then use synchronous 20 Hz.")
        :f?.code==="rtc_delay_exceeded"
        ?text("新动作超过 RTC 时间预算。查看 model_ms 和 recorder_check_ms；若重复发生，使用同步 20 Hz。","New actions exceeded the RTC time budget. Check model_ms and recorder_check_ms; use synchronous 20 Hz if this repeats.")
        :text("Session、录制或运行进程卡住时可重启当前 RLT 运行组件；硬件故障和存储掉线需先处理对应设备。","Restart current RLT runtime components if the Session, recorder or runtime is stuck. Hardware faults and disconnected storage require device repair."))+" "+steps;
      check.textContent=text("重新检查状态","Check status");
      repair.textContent=text("收尾录制并恢复运行进程","Finish recording and recover runtime");
      restart.textContent=text("重启运行组件（保留模型）","Restart runtime components (keep model)");
      repair.hidden=!f;
      output.textContent=text("查看输出","View output");
      check.disabled=busy;
      const blocked=busy||Boolean(state.operation)||Boolean(state.active)||Boolean(state.status_stale);
      repair.disabled=blocked||!f?.recoverable;
      restart.disabled=blocked||!(state.runtime_recovery_available||f?.recoverable);
      if(state.active)adviceNode.textContent+=text(" 先结束当前评测轮次，再恢复运行组件。"," Finish the active evaluation trial before restarting runtime components.");
      result.textContent=message;
    }
    async function act(recover,general=false){
      if((general?restart:recover?repair:check).disabled)return;
      busy=true;message=text("正在处理…","Working…");show();
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),recover?90000:12000);
      try{
        const response=await fetch(recover?"/api/rlt/recover-runtime":"/api/deployment/status",{cache:"no-store",signal:controller.signal,...(recover?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({finalize_pending:true,...(general?{restart_running:true}:{})})}:{})});
        const payload=await response.json();
        if(!response.ok)throw new Error(typeof payload.detail==="string"?payload.detail:JSON.stringify(payload.detail||payload));
        if(recover){
          message=text("恢复已启动，Stage1 保留。等待运行进程就绪后，手动开始 Session；不会自动推理。","Recovery started; Stage1 retained. Wait for the runtime to become ready, then start the session manually. Inference will not start automatically.");
          if(payload.recording_retained?.path)message+="\n"+text("保留录制：","Retained recording: ")+payload.recording_retained.path;
          root.CobotWorkspaceUI?.report(message,"success",text("运行恢复","Runtime recovery"));
          root.CobotOutputPanel?.follow({id:"deployment",component:"deployment"});
        }else{state=payload;message=runtimeSummary(state)||text("运行状态已更新","Runtime status updated");}
        await refresh?.();
      }catch(error){
        if(controller.signal.aborted||error.name==="AbortError"||error instanceof TypeError){
          state={...state,status_stale:true};
          message=text("连接超时或中断，操作结果待确认。先重新检查状态，不要重复恢复。","Connection timed out or was interrupted; the outcome is uncertain. Check status before recovering again.");
        }else message=error.message==="pending_episode_finalization"
          ?text("录制尚未安全完成，数据已保留。查看输出；存储或写入线程恢复后再次操作。","Recording could not finish safely; data is retained. Check output, then retry after storage or the writer recovers.")
          :error.message==="runtime_not_recoverable_with_retained_model"
          ?text("当前不能保留模型恢复：运行进程可能尚未退出，或 Stage1 已退出。请重新检查状态。","Cannot recover with the retained model: runtime processes may still exist, or Stage1 has exited. Check status again.")
          :["recorder_worker_still_active","owned_runtime_still_stopping"].includes(error.message)
          ?text("组件尚未完全停止，未启动重复进程；查看输出与设备/存储状态，恢复后重试。","Components have not fully stopped; no duplicate runtime was started. Check output and device/storage health before retrying.")
          :error.message==="stage1_not_retained"
          ?text("Stage1 已退出，无法保留模型恢复；修复原因后重新加载模型。","Stage1 has exited; recovery cannot retain it. Fix the cause, then reload the model.")
          :error.message;
      }finally{clearTimeout(timer);busy=false;show();}
    }
    check.addEventListener("click",()=>act(false));repair.addEventListener("click",()=>act(true));restart.addEventListener("click",()=>act(true,true));
    output.addEventListener("click",()=>root.CobotOutputPanel?.follow({id:"deployment",component:"deployment"}));
    return {update(next){state=next||{};show();}};
  }
  root.CobotRuntimeRecovery={create:createRuntime,summary:runtimeSummary};
  root.CobotRecorderRecovery={create};
})(window);
