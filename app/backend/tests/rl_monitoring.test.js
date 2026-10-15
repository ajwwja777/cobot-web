const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
function setup(){const dom=new JSDOM('<div id="grid"></div>',{url:'http://localhost',runScripts:'outside-only',pretendToBeVisual:true});dom.window.eval(fs.readFileSync('segmented_frontend/rl_monitoring.js','utf8'));return dom;}
const model={id:'expo',label:'EXPO',runtime_adapter:'expo_online',expo_online_options:{defaults:{rtc_delay:5,edit_scale:.01}}};
const data={live:true,options:{rtc_delay:5,edit_scale:.01},actor_version:2,training:{updated_at:1,points:[{critic_step:20,q:.1,target_q_mean:.12,critic_loss:.01},{critic_step:40,q:.2,target_q_mean:.21,critic_loss:.005}]},success:{points:[{episode:1,rate:50,successes:1,total:2,assisted:1}]},rollout:{episode_uuid:'one',actor_version:2,points:[{seconds:0,q:.1},{seconds:1,q:.2}]}};
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
test('four charts follow controls, show real axes and make GET-only requests',async()=>{
 const dom=setup(),w=dom.window;const calls=[];
 try{w.fetch=async(url,opts)=>{calls.push([url,opts]);return {ok:true,json:async()=>data}};
 const ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{phase:'offline'});await tick();
 assert.equal(w.document.querySelector('#grid').nextElementSibling.id,'collection-rl-monitoring');
 assert.equal(w.document.querySelectorAll('svg').length,4);assert.match(w.document.body.textContent,/Critic 累计更新次数/);assert.match(w.document.body.textContent,/50% · 1\/2/);
 assert.equal(calls.length,1);assert.equal(calls[0][1].method,undefined);
 ui.update(null,{});assert.equal(w.document.querySelector('#collection-rl-monitoring').hidden,false);assert.match(w.document.body.textContent,/当前模式无在线学习/);ui.destroy();
 }finally{dom.window.close();}
});
test('late response from another model cannot populate the new charts',async()=>{
 const dom=setup(),w=dom.window;const pending=[];
 try{w.fetch=()=>new Promise(resolve=>pending.push(resolve));const ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));
 ui.update(model,{phase:'offline'});ui.update({...model,id:'other'},{phase:'offline'});
 pending[0]({ok:true,json:async()=>data});await tick();assert.equal(w.document.querySelectorAll('svg').length,0);
 pending[1]({ok:true,json:async()=>({...data,training:{points:[]},rollout:{points:[]},success:{points:[]}})});await tick();assert.equal(w.document.querySelectorAll('svg').length,0);ui.destroy();
 }finally{dom.window.close();}
});
test('empty history renders missing-data text instead of zero curves; English supported',async()=>{
 const dom=setup(),w=dom.window;
 try{w.CobotPreferences={language:'en'};w.fetch=async()=>({ok:true,json:async()=>({live:false,training:{},success:{points:[]},rollout:{points:[]}})});
 const ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{});await tick();
 assert.equal(w.document.querySelectorAll('svg').length,0);assert.match(w.document.body.textContent,/no historical values inferred/);assert.match(w.document.body.textContent,/History \/ awaiting connection/);ui.destroy();
 }finally{dom.window.close();}
});
