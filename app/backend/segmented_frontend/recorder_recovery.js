"use strict";
(function(root){
  const en=()=>root.CobotPreferences?.language==="en";
  const text=(zh,english)=>en()?english:zh;
  const advice={
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
      container.hidden=state.model?.kind!=="rlt"||state.phase==="offline";
      container.querySelector("summary").textContent=text("录制检查与恢复（保留模型）","Recording checks and recovery (keep model)");
      check.textContent=text("检查录制","Check recording");
      recover.textContent=text("恢复录制（保留模型）","Recover recording (keep model)");
      hint.textContent=state.session?.phase==="fault"
        ?text("录制 Session 异常，模型仍保留。检查原因后可恢复，不必释放权重。","Recording session needs recovery; weights stay loaded. Check the cause, then recover.")
        :text("录制遇到错误时先检查；恢复不会开始推理或释放模型。","Check here if recording fails. Recovery never starts inference or unloads the model.");
      check.disabled=busy||active||Boolean(state.operation);
      recover.disabled=check.disabled||state.session?.policy_paused!==true||!["fault","stopped"].includes(state.session?.phase);
      if(busy){result.textContent=text("正在检查/恢复…","Checking/recovering…");return;}
      if(lastError){result.textContent=lastError;return;}
      if(!last){result.textContent="";return;}
      const p=last.preflight||last.readiness;
      if(p?.status==="ok"){
        result.textContent=last.model_retained
          ?text("已恢复，模型保留。点击“开始 Session”，再手动开始采集。","Recovered; model retained. Start the session, then start capture manually.")
          :last.preflight?text("录制预检通过；相机、关节反馈和目标目录已检查。","Recording preflight passed: cameras, joint feedback and destination checked."):text("实时输入已恢复；点击“检查录制”核对目标目录，或恢复 Session。","Live inputs are ready; check the destination or recover the session.");
      }else if(p){
        result.textContent=(p.error_code||"not_ready")+(p.detail||p.stale_keys?.length?" · "+(p.detail||p.stale_keys.join(", ")):"")+" — "+explanation(p.error_code);
      }
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
      if(fault&&fault!==observedFault){observedFault=fault;container.open=true;
        fetch("/api/rlt/recorder-diagnostics",{cache:"no-store"}).then(parse).then(value=>{last=value;show();}).catch(()=>{});
      }
    }};
  }
  root.CobotRecorderRecovery={create};
})(window);
