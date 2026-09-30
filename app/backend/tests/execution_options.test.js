const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),{JSDOM}=require('jsdom');
const flush=()=>new Promise(r=>setImmediate(r));
function setup(){
 const dom=new JSDOM('<section id="options"></section><section id="labels"></section>',{runScripts:'outside-only',url:'http://localhost'}),w=dom.window;
 w.CobotPreferences={language:'en'};w.confirm=()=>true;
 for(const file of ['execution_options','history_labels'])w.eval(fs.readFileSync('segmented_frontend/'+file+'.js','utf8'));
 return [dom,w];
}
test('Hz RTC and smoothing choices survive polling; unchecked uses model default',()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#options'),ui=w.CobotExecutionOptions.create(node);
 const model={id:'rtc50',kind:'rlt',execution_settings:{publish_hz:50,logical_hz:20,rtc:true,smoothing:true}};
 ui.update(model,{});assert.match(node.textContent,/50 Hz/);assert.deepEqual(JSON.parse(JSON.stringify(ui.value)),{enabled:false});
 const toggle=node.querySelector('.execution-custom');toggle.checked=true;toggle.dispatchEvent(new w.Event('change'));
 const rtc=node.querySelector('.execution-rtc');rtc.checked=false;rtc.dispatchEvent(new w.Event('change'));
 const hz=node.querySelector('.execution-hz');hz.value='30';hz.dispatchEvent(new w.Event('change'));
 ui.update(model,{});assert.equal(ui.value.publish_hz,30);assert.equal(ui.value.rtc,false);assert.equal(ui.value.smoothing,true);
 ui.update(model,{operation:'load'});assert.equal(hz.disabled,true);
 ui.update({id:'normal',kind:'normal'},{});assert.equal(node.hidden,true);assert.equal(ui.value,undefined);
 ui.update(model,{});toggle.checked=false;toggle.dispatchEvent(new w.Event('change'));
 assert.match(node.textContent,/50 Hz/);assert.equal(hz.disabled,true);dom.window.close();
});
test('uncertain apply checks GET before another mutation',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#options'),ui=w.CobotExecutionOptions.create(node),calls=[];
 const model={id:'rtc50',kind:'rlt',execution_settings:{publish_hz:50}};
 ui.update(model,{model,phase:'ready',runtime_recovery_available:true});
 node.querySelector('.execution-custom').checked=true;node.querySelector('.execution-custom').dispatchEvent(new w.Event('change'));
 w.fetch=async(url,options)=>{calls.push([url,options]);if(options.method==='POST')throw new w.TypeError('lost response');return{ok:true,json:async()=>({model,phase:'ready'})};};
 node.querySelector('.execution-apply').click();await flush();
 assert.match(node.textContent,/uncertain/);
 node.querySelector('.execution-apply').click();await flush();
 assert.equal(calls.length,2);assert.equal(calls[0][0],'/api/rlt/recover-runtime');assert.equal(calls[1][0],'/api/deployment/status');assert.equal(calls[1][1].method,undefined);
 dom.window.close();
});
test('unlabelled history can be labelled with version guard; deferred file cannot',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#labels'),ui=w.CobotHistoryLabels.create(node),calls=[];
 w.fetch=async(url,options={})=>{calls.push([url,options]);return{ok:true,json:async()=>({episode_uuid:'u',episode_outcome:options.method?'success':'unknown',keep_for_training:'false',label_updated_at:'v1',replay_modified:false})};};
 await ui.update({episode_uuid:'u',history_format:'rollout'},'/data');
 node.querySelector('.history-outcome').value='success';node.querySelector('.history-save-label').click();await flush();
 const saved=JSON.parse(calls[1][1].body);assert.equal(saved.outcome,'success');assert.equal(saved.expected_label_updated_at,'v1');assert.equal(saved.keep_for_training,false);
 await ui.update({episode_uuid:'broken',history_format:'deferred'},'/data');assert.equal(node.querySelector('.history-save-label').disabled,true);assert.match(node.textContent,/complete validated/);dom.window.close();
});
