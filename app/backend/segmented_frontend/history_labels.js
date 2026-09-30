"use strict";
(function(root){
  const text=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  function create(container){
    container.classList.add('history-labels');
    container.innerHTML='<select class="history-outcome" aria-label="Episode result"><option value="unknown"></option><option value="success"></option><option value="failure"></option></select><span class="history-label-message" role="status" hidden></span>';
    const outcome=container.querySelector('.history-outcome'),message=container.querySelector('.history-label-message');
    let episode=null,dataRoot='',labels=null,token=0,busy=false,notice='',uncertain=false;
    const path=()=>'/api/recordings/'+encodeURIComponent(episode.episode_uuid)+'/labels?data_root='+encodeURIComponent(dataRoot);
    function show(){
      container.hidden=!episode;
      outcome.setAttribute('aria-label',text('Episode 结果','Episode result'));
      outcome.options[0].textContent=text('未知','Unknown');outcome.options[1].textContent=text('成功','Success');outcome.options[2].textContent=text('失败','Failure');
      outcome.disabled=busy||!labels||uncertain;
      message.hidden=!notice;message.textContent=notice;outcome.title=notice;
    }
    async function request(url,options={}){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
      try{const response=await fetch(url,{...options,signal:controller.signal}),payload=await response.json();if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:'label_request_failed');return payload;}
      finally{clearTimeout(timer);}
    }
    function accept(payload){labels=payload;outcome.value=['success','failure'].includes(payload.episode_outcome)?payload.episode_outcome:'unknown';episode.episode_outcome=payload.episode_outcome;root.preloadedRltLabels?.delete?.(dataRoot+'|'+episode.episode_uuid);document.dispatchEvent(new CustomEvent('cobot:history-label-saved',{detail:{episode_uuid:episode.episode_uuid,data_root:dataRoot,labels:payload}}));}
    outcome.addEventListener('change',async()=>{
      if(outcome.disabled)return;const selected=token,url=path(),chosen=outcome.value,uuid=episode.episode_uuid;busy=true;notice='';show();
      try{const payload=await request(url,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({episode_uuid:uuid,outcome:chosen,expected_label_updated_at:labels.label_updated_at??null})});if(selected===token)accept(payload);}
      catch(error){if(selected!==token)return;uncertain=true;notice=text('正在核对结果…','Checking result…');show();try{const payload=await request(url,{cache:'no-store'});if(selected!==token)return;accept(payload);uncertain=false;notice=payload.episode_outcome===chosen?'':text('未保存：','Not saved: ')+error.message;}catch(_){if(selected===token)notice=text('结果待确认；重新选择该 Episode 以检查。','Result pending; reselect this episode to check.');}}
      finally{if(selected===token){busy=false;show();}}
    });
    document.addEventListener('cobot:language',show);
    return {async update(next,rootPath){const selected=++token;episode=next;dataRoot=rootPath;labels=null;busy=false;uncertain=false;notice='';outcome.value=['success','failure'].includes(next?.episode_outcome)?next.episode_outcome:'unknown';show();if(!next)return;
      if(next.history_format==='deferred'){outcome.title=text('录制尚未完成，可稍后处理','Recording incomplete; handle later');return;}
      try{const payload=await request(path(),{cache:'no-store'});if(selected!==token)return;accept(payload);}
      catch(error){if(selected===token)notice=error.message;}
      finally{if(selected===token)show();}
    }};
  }
  root.CobotHistoryLabels={create};
})(window);
