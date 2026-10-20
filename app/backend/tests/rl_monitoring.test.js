const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
function setup(){const dom=new JSDOM('<div id="grid"></div>',{url:'http://localhost',runScripts:'outside-only',pretendToBeVisual:true});dom.window.eval(fs.readFileSync('segmented_frontend/rl_monitoring.js','utf8'));return dom;}
const model={id:'expo',label:'EXPO',runtime_adapter:'expo_online',expo_online_options:{defaults:{rtc_delay:5,edit_scale:.01}}};
const data={live:true,options:{rtc_delay:5,edit_scale:.01},actor_version:2,training:{updated_at:1,points:[{critic_step:20,q:.1,target_q_mean:.12,critic_loss:.01},{critic_step:40,q:.2,target_q_mean:.21,critic_loss:.005}]},success:{points:[{episode:1,rate:50,successes:1,total:2,assisted:1}]},rollout:{episode_uuid:'one',actor_version:2,points:[{seconds:0,q:.1},{seconds:1,q:.2}]}};
const tick=()=>new Promise(resolve=>setTimeout(resolve,20));
test('charts follow controls, show real axes and make GET-only requests',async()=>{
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
test('independent learner remains live with unloaded Actor; stopped and disconnected are distinct',async()=>{
 const dom=setup(),w=dom.window;
 try{
  let value={...data,actor_version:null,actor_loaded:false,learner:{supported:true,connected:true,running:true,learner_version:49}};
  w.fetch=async()=>({ok:true,json:async()=>value});
  let ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{phase:'offline'});await tick();
  assert.match(w.document.body.textContent,/Learner 运行中 · Actor 未加载/);assert.match(w.document.body.textContent,/Learner 49/);assert.equal(w.document.querySelectorAll('svg').length,4);ui.destroy();
  value={...value,live:false,learner:{supported:true,connected:true,running:false}};
  ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{phase:'offline'});await tick();assert.match(w.document.body.textContent,/训练已停止 · 历史记录/);ui.destroy();
  value={...value,learner:{supported:true,connected:false,running:false}};
  ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{phase:'offline'});await tick();assert.match(w.document.body.textContent,/指标连接中断 · 保留曲线/);assert.equal(w.document.querySelectorAll('svg').length,4);ui.destroy();
 }finally{dom.window.close();}
});

test('RLT Online reuses monitoring plots with Q1 semantics and no EXPO parameter query',async()=>{
 const dom=setup(),w=dom.window,calls=[];
 try{
  w.fetch=async(url)=>{calls.push(url);return {ok:true,json:async()=>({...data,algorithm:'rlt',rollout:{...data.rollout,critic_version:7000}})}};
  const ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));
  const rlt={id:'rlt-online',kind:'rlt',mode:'online',training_enabled:true,label:'RLT Online'};
  ui.update(rlt,{phase:'offline'});await tick();
  assert.equal(w.document.querySelectorAll('svg').length,4);
  assert.match(w.document.body.textContent,/平均 Q1/);
  assert.match(w.document.body.textContent,/Q1 与 Q2/);
  assert.match(w.document.body.textContent,/Critic 7000/);
  assert.doesNotMatch(calls[0],/rtc_delay|edit_scale/);
  ui.update({...rlt,mode:'frozen',training_enabled:false},{});
  assert.match(w.document.body.textContent,/当前模式无在线学习/);ui.destroy();
 }finally{w.close();}
});
test('episode selection keeps historical Q while current episode advances and can return live',async()=>{
 const dom=setup(),w=dom.window,calls=[];
 try{
  w.fetch=async(url)=>{calls.push(url);const historical=url.includes('episode=old');return {ok:true,json:async()=>({...data,rollout_episodes:[{episode_uuid:'old',started_at:1,actor_version:3}],rollout:historical?{episode_uuid:'old',actor_version:3,points:[{seconds:1,q:.8}],historical:true}:data.rollout})}};
  const ui=w.CobotRLMonitoring.create(w.document.querySelector('#grid'));ui.update(model,{});await tick();
  const picker=w.document.querySelector('.rl-episode-picker');assert.equal(picker.options.length,2);
  picker.value='old';picker.dispatchEvent(new w.Event('change'));await tick();
  const chunkCard=[...w.document.querySelectorAll('.rl-chart-card')].find(c=>c.querySelector('h4').textContent==='回合回放 · 动作块 Critic 评分');assert(chunkCard);assert.match(chunkCard.textContent,/已记录动作块序号/);assert.match(chunkCard.querySelector('circle title').textContent,/动作块序号: 1[\s\S]*Selected Q: 0.8/);assert(![...w.document.querySelectorAll('h4')].some(h=>h.textContent==='回合时长'));assert.match(calls.at(-1),/episode=old/);assert.match(w.document.body.textContent,/历史回合 · old · Actor 3/);
  ui.update(model,{phase:'offline'});assert.equal(picker.value,'old');
  picker.value='';picker.dispatchEvent(new w.Event('change'));await tick();
  assert.doesNotMatch(calls.at(-1),/episode=/);assert.match(w.document.body.textContent,/最近记录回合 · one/);ui.destroy();
 }finally{w.close();}
});
