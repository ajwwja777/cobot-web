"use strict";
(function(root){
  const text=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  function create(container){
    container.classList.add('history-labels');
    container.innerHTML='<label><span class="history-outcome-label"></span><select class="history-outcome"><option value="unknown">Unlabelled</option><option value="success">Success</option><option value="failure">Failure</option></select></label><label><span class="history-note-label"></span><input class="history-label-note" maxlength="500"></label><label><input type="checkbox" class="history-keep-training"><span class="history-keep-label"></span></label><button type="button" class="history-save-label secondary"></button><p class="history-label-message" role="status"></p>';
    const outcome=container.querySelector('.history-outcome'),note=container.querySelector('.history-label-note'),keep=container.querySelector('.history-keep-training'),save=container.querySelector('.history-save-label'),message=container.querySelector('.history-label-message');
    let episode=null,dataRoot='',labels=null,token=0,busy=false,notice='',uncertain=false;
    const path=()=>'/api/recordings/'+encodeURIComponent(episode.episode_uuid)+'/labels?data_root='+encodeURIComponent(dataRoot);
    function show(){
      container.hidden=!episode;
      container.querySelector('.history-outcome-label').textContent=text('轮次结果（补标签 / 重标注）','Episode outcome (label / relabel)');
      container.querySelector('.history-note-label').textContent=text('备注','Note');
      container.querySelector('.history-keep-label').textContent=text('允许后续离线训练使用','Allow later offline training');
      outcome.options[0].textContent=text('未标注 / 暂存','Unlabelled / deferred');outcome.options[1].textContent=text('成功','Success');outcome.options[2].textContent=text('失败','Failure');
      save.textContent=busy?text('处理中…','Working…'):uncertain?text('检查标注结果','Check label result'):text('保存结果标注','Save outcome label');
      outcome.disabled=note.disabled=keep.disabled=busy||!labels||uncertain;
      save.disabled=busy||!labels;
      message.textContent=notice||text('只修改所选历史录制的标签，不启动推理，也不修改已提交的 Replay。','Updates this historical recording only; no inference or changes to existing Replay.');
    }
    async function request(url,options={}){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
      try{return await fetch(url,{...options,signal:controller.signal});}
      finally{clearTimeout(timer);}
    }
    async function read(){
      const response=await request(path(),{cache:'no-store'}),payload=await response.json();
      if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:'label_read_failed');
      return payload;
    }
    save.addEventListener('click',async()=>{
      if(save.disabled)return;const selected=token;busy=true;show();
      try{
        if(uncertain){const checked=await read();if(selected!==token)return;labels=checked;uncertain=false;outcome.value=labels.episode_outcome||'unknown';note.value=labels.operator_note||'';keep.checked=labels.keep_for_training==='true';notice=text('已重新读取结果。','Result rechecked.');return;}
        const response=await request(path(),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({episode_uuid:episode.episode_uuid,outcome:outcome.value,operator_note:note.value,keep_for_training:keep.checked,expected_label_updated_at:labels.label_updated_at??null})});
        const payload=await response.json();if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:'label_save_failed');
        if(selected!==token)return;labels=payload;episode.episode_outcome=payload.episode_outcome;
        root.preloadedRltLabels?.delete?.(dataRoot+'|'+episode.episode_uuid);
        notice=text('标签已保存，Replay 未改。','Labels saved; Replay unchanged.');
        document.dispatchEvent(new CustomEvent('cobot:history-label-saved',{detail:{episode_uuid:episode.episode_uuid,labels:payload}}));
      }catch(error){if(selected===token){uncertain=error instanceof TypeError||error.name==='AbortError';notice=uncertain?text('连接中断，结果待确认；先检查，不重复提交。','Connection interrupted; check the result before resubmitting.'):error.message;}}
      finally{if(selected===token){busy=false;show();}}
    });
    document.addEventListener('cobot:language',show);
    return {async update(next,rootPath){const selected=++token;episode=next;dataRoot=rootPath;labels=null;busy=false;uncertain=false;notice='';show();if(!next)return;
      if(next.history_format==='deferred'){notice=text('未完成或损坏的暂存文件已保留，可稍后删除；通过完整性校验前不能标为成功/失败。','Interrupted or invalid file retained; delete it later if needed. Success/failure labels require a complete validated recording.');show();return;}
      try{const payload=await read();if(selected!==token)return;labels=payload;outcome.value=payload.episode_outcome||'unknown';note.value=payload.operator_note||'';keep.checked=payload.keep_for_training==='true';}
      catch(error){if(selected===token)notice=error.message;}
      finally{if(selected===token)show();}
    }};
  }
  root.CobotHistoryLabels={create};
})(window);
