const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('jsdom');
function setup(){const d=new JSDOM('<main></main>',{runScripts:'outside-only'});d.window.eval(fs.readFileSync('segmented_frontend/expo_progress.js','utf8'));return d;}
test('separate checkpoint, published, received and served; stale source never claims training live',()=>{
 const d=setup(),w=d.window,ui=w.CobotExpoProgress.create(w.document.querySelector('main'));
 const run={observed_at:Date.now()/1000,progress:{async_mode:'native_async_v1',learner_step:102,committed_learner_step:102,published_actor_version:98,downloaded_actor_version:89,served_actor_version:89,critic_updates:2040,actor_updates:102,learner_phase:'waiting_episode',updated_at:10,network_error:'disconnected',download:{state:'retrying',actor_version:98},transfer:{bytes_received:7e8,bytes_total:2e9,percent:35}},version_events:[]};
 ui.update(run,{});assert.match(w.document.body.textContent,/102/);assert.match(w.document.body.textContent,/98/);assert.match(w.document.body.textContent,/89/);assert.match(w.document.body.textContent,/35.0%/);assert.match(w.document.body.textContent,/连接中断/);assert.match(w.document.body.textContent,/实际抽样组成.*未记录/);assert.equal(w.document.querySelectorAll('.expo-stage').length,5);
 ui.update({progress:{learner_step:0,served_actor_version:0},version_events:[]},{});assert.match(w.document.querySelector('[data-stage="served"]').textContent,/0/);assert.doesNotMatch(w.document.body.textContent,/35.0%/);
 d.window.close();
});
test('unknown transfer size has no fabricated percentage; text is safe',()=>{const d=setup(),w=d.window,ui=w.CobotExpoProgress.create(w.document.querySelector('main'));ui.update({progress:{download:{state:'retrying',actor_version:98,error:'<img src=x onerror=alert(1)>'},transfer:{bytes_received:123}},version_events:[]},{});assert.equal(w.document.querySelector('progress').hasAttribute('value'),false);assert.equal(w.document.querySelector('img'),null);d.window.close();});

test('normal 60-second optimizer group is not reported stale at 45 seconds',()=>{const d=setup(),w=d.window,ui=w.CobotExpoProgress.create(w.document.querySelector('main'));ui.update({progress:{learner_phase:'updating',updated_at:Date.now()/1000-45,timings:{update_seconds:62}},version_events:[]},{});assert.match(w.document.body.textContent,/训练更新中/);assert.doesNotMatch(w.document.body.textContent,/状态已过期/);d.window.close();});
