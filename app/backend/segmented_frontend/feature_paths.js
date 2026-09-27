"use strict";
// Read-only provenance. Existing renderers supply live selections; no polling or writes.
(function(root){
  const P='/media/agilex/Getea1/jiaan/projects/cobot-platform';
  const contexts=new Map(),sections=new Map();let manifest=null;
  const $=s=>document.querySelector(s),t=(zh,en)=>root.CobotPreferences?.language==='en'?en:zh;
  const row=(zh,en,path,host='Cobot')=>[zh,en,path,host];
  const pathValue=v=>typeof v==='string'&&v.startsWith('/');
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  async function copy(path,button){
    try{
      if(navigator.clipboard&&root.isSecureContext)await navigator.clipboard.writeText(path);
      else{
        const focused=document.activeElement,selection=root.getSelection(),ranges=[];
        if(selection)for(let i=0;i<selection.rangeCount;i++)ranges.push(selection.getRangeAt(i).cloneRange());
        const field=node('textarea');field.value=path;field.className='path-copy-buffer';document.body.append(field);
        let ok=false;try{field.focus({preventScroll:true});field.select();ok=document.execCommand('copy');}finally{field.remove();focused?.focus({preventScroll:true});if(selection){selection.removeAllRanges();ranges.forEach(r=>selection.addRange(r));}}
        if(!ok)throw Error('clipboard');
      }
      button.textContent=t('已复制','Copied');
    }catch(_){button.textContent=t('请选中复制','Select to copy');}
    root.setTimeout(()=>{if(button.isConnected)button.textContent=t('复制','Copy');},1500);
  }
  function decorateValue(container,path){
    if(!pathValue(path))return;
    container.replaceChildren(node('code',path,'feature-path-value'));container.classList.add('feature-path-inline');
    const button=node('button',t('复制','Copy'),'feature-path-copy');button.type='button';button.setAttribute('aria-label',t('复制路径','Copy path')+' '+path);button.addEventListener('click',()=>copy(path,button));container.append(button);
  }
  function put(id,target,rows){
    const parent=typeof target==='string'?$(target):target;if(!parent)return;
    rows=rows.filter(r=>pathValue(r?.[2]));
    let details=sections.get(id);
    if(!details){details=node('details',null,'feature-paths');details.id='paths-'+id;details.append(node('summary',t('路径','Paths')));const list=node('dl',null,'feature-path-list');details.append(list);sections.set(id,details);parent.append(details);}
    if(details.parentElement!==parent)parent.append(details);
    const signature=JSON.stringify([t('路径','Paths'),rows]);if(signature===details.dataset.signature)return;
    details.dataset.signature=signature;details.querySelector('summary').textContent=t('路径','Paths');
    const list=details.querySelector('dl');list.replaceChildren();
    for(const [zh,en,path,host] of rows){const key=node('dt',t(zh,en)),badge=node('span',host||'Cobot','feature-path-host');key.append(badge);const value=node('dd');decorateValue(value,path);list.append(key,value);}
    if(!rows.length)list.append(node('dd',t('暂无路径','No paths available')));
  }
  function logRows(jobs,components){return Object.entries(jobs||{}).filter(([key,j])=>components.includes(key)&&pathValue(j.log_path)).map(([key,j])=>row('最近日志 · '+key,'Latest log · '+key,j.log_path));}
  function render(key){
    if(!manifest)return;const d=contexts.get(key)||{};
    if(key==='devices'){
      const camera=String(d.focus||'').startsWith('camera'),computer=d.focus==='computer';
      const kind=computer?'computer':camera?'camera':'robot';
      const rows=[...manifest[kind],...logRows(d.devices?.jobs,computer?['can','roscore','arms','cameras']:camera?['cameras']:['home','pose','recover','arms'])];
      if(d.devices?.pose_config_path&&!computer&&!camera)rows.splice(2,1,row('位姿配置','Pose configuration',d.devices.pose_config_path));
      if(computer){const s=root.CobotDeploymentUI?.state,m=contexts.get('rl')?.model;if(['ready','paused','running'].includes(s?.phase))rows.unshift(row('已加载模型权重','Loaded model weights',s.model?.checkpoint));else if(m)rows.unshift(row('RLT 下次启动权重','RLT next-start weights',m.checkpoint));}
      put('devices','.spatial-detail',rows);
    }else if(key==='history'){
      const e=d.episode||{},base=String(d.dataRoot||'').replace(/\/$/,'');let rows=[];
      if(base)rows.push(row('当前历史目录','Current history directory',base));
      // Source paths come from the current episode metadata, not a previously viewed row.
      let relative=e.source_hdf5_relative;
      if(d.rlt&&Number.isInteger(e.episode_index))relative='episode_'+String(e.episode_index).padStart(6,'0')+'.hdf5';
      if(base&&relative&&e.episode_uuid){
        const file=base+'/'+relative;
        rows.push(row('录制文件','Recording file',file));
        if(d.rlt&&e.has_labels)rows.push(row('标签文件','Labels file',file.replace(/\.hdf5$/,'.labels.json')));
        if(!d.rlt)rows.push(row('节点记录目录','Marker directory',base+'/.segments/'+relative.split('/').slice(0,-1).concat(e.episode_uuid).join('/')));
        rows.push(row('回放缓存目录','Replay cache directory',file.slice(0,file.lastIndexOf('/'))+'/.previews/'+e.episode_uuid));
      }
      put('history','#episode-browser-panel',[...rows,...manifest[d.rlt?'history-rlt':'history-normal']]);
    }else if(key==='normal'){
      put('normal','#capture-form',pathValue(d.dataRoot)?[row('所选录制目录','Selected recording directory',d.dataRoot)]:[]);
      put('normal-actions','[data-page=operation] .action-panel',manifest.normal);
    }else if(key==='rl'){
      const m=d.model||{},chosen=d.chosen||m;
      put('rl','.rl-process-panel',[
        row('下次启动权重','Next-start weights',m.checkpoint),
        ...(chosen.id!==m.id?[row('待应用选择','Unapplied selection',chosen.checkpoint)]:[]),
        row('运行目录','Run directory',m.run_root),...manifest.rl]);
      put('rl-storage',$('#rlt-data-root')?.closest('article'),[row('当前保存目录','Current recording directory',d.dataRoot)]);
    }else if(key==='training'){
      put('training',$('#training-facts')?.closest('article'),manifest.training[d.run]||[]);
      put('diagnostics',$('#diag-update-card'),manifest.diagnostics);
      document.querySelectorAll('#training-figures figure').forEach((figure,index)=>{
        const img=figure.querySelector('img'),url=img?.getAttribute('src');
        if(url?.startsWith('/assets/'))put('figure-'+index,figure,[row('图表文件','Chart file',P+'/app/backend/segmented_frontend'+url)]);
      });
    }else if(key==='deployment'){
      const m=d.model||{},s=d.state||{};
      put('deployment','.deployment-model-panel',[row('所选模型权重','Selected model weights',m.checkpoint),row('基础模型','Base model',m.base_checkpoint),row('当前已加载权重','Currently loaded weights',['ready','paused','running'].includes(s.phase)?s.model?.checkpoint:null),...manifest.deploy]);
      put('deployment-controls','.deployment-control-panel',[row('当前保存目录','Current results directory',s.data_root),row('最近部署日志','Latest deployment log',s.log_path),...manifest['deploy-controls']]);
    }else if(key==='deployment-record'){
      const r=d.record,rows=[];if(r){const base=r.data_root+'/'+r.id;rows.push(row('结果记录','Result record',base+'/result.json'));for(const [which,zh,en] of [['start','首帧','First frame'],['end','末帧','Last frame']])for(const [i,path] of (r[which]?.files||[]).entries())rows.push(row(zh+' · '+(i+1),en+' · '+(i+1),pathValue(path)?path:base+'/'+path));}
      put('deployment-record','.deployment-results-panel',rows);
    }else if(key==='static'){
      put('camera','#camera-dock',manifest.camera);put('outputs','[data-page=outputs]',manifest.outputs);put('host','[data-page=host]',manifest.host);
    }
  }
  function update(key,data){contexts.set(key,data);render(key);}
  root.CobotFeaturePaths={update,decorateValue,put};
  document.addEventListener('cobot:language',()=>{for(const key of contexts.keys())render(key);});
  fetch('/feature_paths.json?v=20260926-paths-v1',{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error(r.status);return r.json();}).then(data=>{manifest=data;update('static',{});for(const key of contexts.keys())render(key);}).catch(error=>{console.error('Path catalog unavailable',error);});
  document.addEventListener('DOMContentLoaded',()=>{for(const key of contexts.keys())render(key);});
})(globalThis);
