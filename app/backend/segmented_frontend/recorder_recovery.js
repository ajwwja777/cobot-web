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
    container.innerHTML='<summary></summary><p class="recorder-recovery-hint"></p><div class="button-row"><button type="button" class="recorder-check secondary"></button><button type="button" class="recorder-recover secondary"></button></div><p class="recorder-recovery-result" role="status"></p>';
    const hint=container.querySelector(".recorder-recovery-hint"),check=container.querySelector(".recorder-check"),recover=container.querySelector(".recorder-recover"),result=container.querySelector(".recorder-recovery-result");
    let state={},active=false,busy=false,last=null,lastError="",observedFault="";
    function show(){
      container.hidden=state.model?.kind!=="rlt"||state.phase==="offline"||Boolean(state.runtime_failure);
      container.querySelector("summary").textContent=text("录制检查与恢复（保留模型）","Recording checks and recovery (keep model)");
      check.textContent=text("检查录制","Check recording");
      recover.textContent=text("恢复录制（保留模型）","Recover recording (keep model)");
      hint.textContent=state.session?.phase==="fault"
        ?text("录制 Session 异常，模型仍保留。检查原因后可恢复，不必释放权重。","Recording session needs recovery; weights stay loaded. Check the cause, then recover.")
        :text("录制遇到错误时先检查；恢复不会开始推理或释放模型。","Check here if recording fails. Recovery never starts inference or unloads the model.");
      if(state.session?.fault_reason)hint.textContent+=" "+state.session.fault_reason;
      check.disabled=busy||active||Boolean(state.operation);
      recover.disabled=check.disabled||state.session?.policy_paused!==true||!["fault","stopped"].includes(state.session?.phase);
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
        const data_root=root.document.querySelector("#collection-data-root")?.value.trim();
        last=await parse(await fetch(repair?"/api/rlt/recover-recorder":"/api/rlt/recorder-check",{
          method:"POST",headers:{"Content-Type":"application/json"},
          body:JSON.stringify({data_root,...(repair?{reset_fault_session:true}:{})})
        }));
        await root.CobotCollectionModel?.refresh();
        await root.refreshConsole?.();
      }catch(error){lastError=error.message;}
      finally{busy=false;show();}
    }
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
    container.innerHTML='<strong class="runtime-fault-title"></strong><p class="runtime-fault-cause"></p><p class="runtime-fault-advice"></p><div class="button-row"><button type="button" class="runtime-check secondary"></button><button type="button" class="runtime-recover secondary"></button><button type="button" class="runtime-output secondary"></button></div><p class="runtime-recovery-result"></p>';
    const title=container.querySelector(".runtime-fault-title"),cause=container.querySelector(".runtime-fault-cause"),adviceNode=container.querySelector(".runtime-fault-advice");
    const check=container.querySelector(".runtime-check"),repair=container.querySelector(".runtime-recover"),output=container.querySelector(".runtime-output"),result=container.querySelector(".runtime-recovery-result");
    let state={},busy=false,message="";
    function show(){
      const f=state.runtime_failure;
      container.hidden=!f;
      title.textContent=runtimeSummary(state);
      cause.textContent=f?.cause||"";
      const timing=["rtc_delay_exceeded","execution_clock_late"].includes(f?.code);
      adviceNode.textContent=timing
        ?text("新动作未在时间窗口内就绪，执行已停止。先检查输出中的延迟；可保留模型恢复运行进程。若重复发生，停止本轮并切回原同步 20 Hz 配置，不要反复开始。","New actions missed the timing window; execution stopped. Check latency in the output, then recover the runtime while keeping the model. If this repeats, end the episode and return to the original synchronous 20 Hz profile rather than repeatedly starting.")
        :text("检查具体原因后恢复。此按钮仅恢复运行进程，不释放 Stage1、不开始推理、不归位，也不会处理未完成的数据。","Check the cause before recovery. This button restores runtime processes; it does not unload Stage1, start inference, home arms or finalize pending recordings.");
      check.textContent=text("重新检查状态","Check status");
      repair.textContent=text("恢复运行进程（保留模型）","Recover runtime (keep model)");
      output.textContent=text("查看输出","View output");
      check.disabled=busy;repair.disabled=busy||Boolean(state.operation)||Boolean(state.active)||Boolean(state.status_stale)||!f?.recoverable;
      result.textContent=message;
    }
    async function act(recover){
      if((recover?repair:check).disabled)return;
      busy=true;message=text("正在处理…","Working…");show();
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
      try{
        const response=await fetch(recover?"/api/rlt/recover-runtime":"/api/deployment/status",{cache:"no-store",signal:controller.signal,...(recover?{method:"POST"}:{})});
        const payload=await response.json();
        if(!response.ok)throw new Error(typeof payload.detail==="string"?payload.detail:JSON.stringify(payload.detail||payload));
        if(recover){
          message=text("恢复已启动，Stage1 保留。等待运行进程就绪后，手动开始 Session；不会自动推理。","Recovery started; Stage1 retained. Wait for the runtime to become ready, then start the session manually. Inference will not start automatically.");
          root.CobotWorkspaceUI?.report(message,"success",text("运行恢复","Runtime recovery"));
          root.CobotOutputPanel?.follow({id:"deployment",component:"deployment"});
        }else{state=payload;message=runtimeSummary(state)||text("运行状态已更新","Runtime status updated");}
        await refresh?.();
      }catch(error){
        if(controller.signal.aborted||error.name==="AbortError"||error instanceof TypeError){
          state={...state,status_stale:true};
          message=text("连接超时或中断，操作结果待确认。先重新检查状态，不要重复恢复。","Connection timed out or was interrupted; the outcome is uncertain. Check status before recovering again.");
        }else message=error.message==="pending_episode_finalization"
          ?text("仍有待收尾的录制，未重启运行进程，也未删除数据。先处理当前录制并查看输出。","A recording is pending. Runtime was not restarted and data was not deleted. Finish the recording and inspect output first.")
          :error.message==="runtime_not_recoverable_with_retained_model"
          ?text("当前不能保留模型恢复：运行进程可能尚未退出，或 Stage1 已退出。请重新检查状态。","Cannot recover with the retained model: runtime processes may still exist, or Stage1 has exited. Check status again.")
          :error.message;
      }finally{clearTimeout(timer);busy=false;show();}
    }
    check.addEventListener("click",()=>act(false));repair.addEventListener("click",()=>act(true));
    output.addEventListener("click",()=>root.CobotOutputPanel?.follow({id:"deployment",component:"deployment"}));
    return {update(next){state=next||{};show();}};
  }
  root.CobotRuntimeRecovery={create:createRuntime,summary:runtimeSummary};
  root.CobotRecorderRecovery={create};
})(window);
