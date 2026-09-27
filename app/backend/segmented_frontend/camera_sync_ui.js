"use strict";
(function expose(root) {
  function transition(previous, state) {
    const generation = Number(state && state.generation || 0);
    return {generation, refresh:Boolean(state && state.status === 'ready' && (!previous || previous.generation !== generation)), status:String(state && state.status || 'unavailable')};
  }
  function warning(state) {
    const status = String(state && state.status || 'unavailable');
    if (status === 'ready') return '三相机同步 · skew ' + Math.round(Number(state.skew_ms || 0)) + ' ms';
    if (status === 'desynced') return '三相机不同步 · skew ' + Math.round(Number(state.skew_ms || 0)) + ' ms，正在重连';
    if (status === 'frozen') return '画面冻结 ' + Number(state.last_advance_age_sec || 0).toFixed(1) + ' s，正在重连';
    if (status === 'stale') return '相机帧过期：' + ((state.stale_keys || []).join(', ') || 'unknown') + '，正在重连';
    return '相机暂不可用，正在重连';
  }
  function retryDelay(status, failures) {
    // Frames use persistent MJPEG streams; this poll only updates health/FPS.
    if (status === 'ready') return 500;
    return Math.min(2000, 250 * Math.pow(2, Math.max(0, Number(failures || 0))));
  }
  const api={transition,warning,retryDelay};
  root.CobotCameraSyncUI=api;
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
