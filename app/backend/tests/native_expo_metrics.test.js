const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setTimeout(r,20));
test('native charts preserve raw values, parameter/model identity and GET-only behavior',async()=>{
 const dom=new JSDOM('<main></main>',{url:'http://localhost',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,calls=[];
 w.eval(fs.readFileSync('segmented_frontend/rl_monitoring.js','utf8'));w.eval(fs.readFileSync('segmented_frontend/native_expo_metrics.js','utf8'));
 const run={model_id:'expo',options:{rtc_delay:5,edit_scale:.01},observed_at:Date.now()/1000,progress:{critic_updates:40,actor_updates:2},points:[{critic_step:20,actor_step:1,metrics:{actor_loss:.2,filter_critic_loss:.4,edit_actor_loss:-2,select_ratio_with_edit:.7},timings:{update_seconds:3}}]};
 w.fetch=async(url,opts)=>{calls.push([url,opts]);return{ok:true,json:async()=>({schema:1,runs:[run]})}};
 const ui=w.CobotNativeExpoMetrics.create(w.document.querySelector('main'));ui.update({id:'expo',runtime_adapter:'expo_online'},{rtc_delay:5,edit_scale:.01});await tick();
 assert.equal(w.document.querySelectorAll('svg').length,5);assert.match(w.document.body.textContent,/actor_loss/);assert.match(w.document.body.textContent,/不是机械臂执行比例/);assert.equal(calls[0][1].method,undefined);assert.equal(calls[0][0],'/native-expo-metrics.json');
 run.progress.network_error='refused';ui.update({id:'expo',runtime_adapter:'expo_online'},null);assert.match(w.document.body.textContent,/上游连接中断/);assert.match(w.document.body.textContent,/无需 W&B/);assert.doesNotMatch(w.document.body.textContent,/暂未配置上传/);
 ui.update({id:'other',runtime_adapter:'expo_online'},null);assert.equal(w.document.querySelectorAll('svg').length,0);await tick();assert.equal(w.document.querySelectorAll('svg').length,0);
 ui.update({id:'expo',runtime_adapter:'expo_online'},{rtc_delay:3,edit_scale:.01});assert.equal(w.document.querySelectorAll('svg').length,0);
 ui.destroy();dom.window.close();
});
