"use strict";
(function expose(root) {
  function transition(previous, state) {
    const generation = Number(state && state.generation || 0);
    return {generation, refresh:Boolean(state && state.status === 'ready' && (!previous || previous.generation !== generation)), status:String(state && state.status || 'unavailable')};
  }
  function cameraStatus(state,key) {
    return state&&state.cameras?String(state.cameras[key]?.status||'unavailable'):String(state?.status||'unavailable');
  }
  function warning(state) {
    const status = String(state && state.status || 'unavailable');
    if (state && state.cameras) {
      const labels={camera_high:'顶部',camera_left:'左腕',camera_right:'右腕'};
      const keys=Object.keys(labels);
      const ready=keys.filter(key=>cameraStatus(state,key)==='ready');
      const missing=keys.filter(key=>!ready.includes(key)).map(key=>labels[key]);
      if(!missing.length) return status==='desynced'?'三路在线 · 独立预览 · 时间偏差 '+Math.round(Number(state.skew_ms||0))+' ms':'三路在线 · 独立预览';
      return missing.join('、')+'相机不可用 · '+(ready.length?'其余 '+ready.length+' 路继续预览':'等待连接');
    }
    if (status === 'ready') return '三相机同步 · skew ' + Math.round(Number(state.skew_ms || 0)) + ' ms';
    if (status === 'desynced') return '三相机不同步 · skew ' + Math.round(Number(state.skew_ms || 0)) + ' ms，正在重连';
    if (status === 'frozen') return '画面冻结 ' + Number(state.last_advance_age_sec || 0).toFixed(1) + ' s，正在重连';
    if (status === 'stale') return '相机帧过期：' + ((state.stale_keys || []).join(', ') || 'unknown') + '，正在重连';
    return '相机暂不可用，正在重连';
  }
  function retryDelay(status, failures) {
    if (['ready','degraded','desynced'].includes(status)) return 500;
    return Math.min(2000, 250 * Math.pow(2, Math.max(0, Number(failures || 0))));
  }
  function renderHealth(scope,state) {
    for(const image of scope.querySelectorAll('img[data-camera]')) {
      const status=cameraStatus(state,image.dataset.camera);
      image.dataset.cameraStatus=status;
      const figure=image.closest('figure');
      if(!figure)continue;
      figure.dataset.cameraStatus=status;
      let message=figure.querySelector('.camera-unavailable-message');
      if(!message){message=image.ownerDocument.createElement('div');message.className='camera-unavailable-message';message.setAttribute('role','status');figure.appendChild(message);}
      message.hidden=status==='ready';
      message.textContent=status==='frozen'?'画面已冻结':status==='stale'?'相机已断线 · 等待重连':'相机暂不可用 · 等待连接';
    }
  }
  const api={transition,warning,retryDelay,cameraStatus,renderHealth};
  root.CobotCameraSyncUI=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
