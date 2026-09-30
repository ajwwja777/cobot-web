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
 ui.update(model,{});assert.equal(node.querySelector('.execution-hz').value,'50');assert.equal(node.querySelector('.execution-summary'),null);assert.deepEqual(JSON.parse(JSON.stringify(ui.value)),{enabled:false});
 const toggle=node.querySelector('.execution-custom');toggle.checked=true;toggle.dispatchEvent(new w.Event('change'));
 const rtc=node.querySelector('.execution-rtc');rtc.checked=false;rtc.dispatchEvent(new w.Event('change'));
 const hz=node.querySelector('.execution-hz');hz.value='30';hz.dispatchEvent(new w.Event('change'));
 ui.update(model,{});assert.equal(ui.value.publish_hz,30);assert.equal(ui.value.rtc,false);assert.equal(ui.value.smoothing,true);
 ui.update(model,{operation:'load'});assert.equal(hz.disabled,true);
 ui.update({id:'normal',kind:'vla'},{});assert.equal(node.hidden,false);assert.equal(ui.value.enabled,false);
 ui.update(model,{});toggle.checked=false;toggle.dispatchEvent(new w.Event('change'));
 assert.equal(node.querySelector('.execution-hz').value,'50');assert.equal(node.querySelector('.execution-summary'),null);assert.equal(hz.disabled,true);dom.window.close();
});
test('uncertain apply checks GET before another mutation',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#options'),ui=w.CobotExecutionOptions.create(node),calls=[];
 const model={id:'rtc50',kind:'rlt',execution_settings:{publish_hz:50}};
 ui.update(model,{model,phase:'ready',runtime_recovery_available:true});
 node.querySelector('.execution-custom').checked=true;node.querySelector('.execution-custom').dispatchEvent(new w.Event('change'));
 w.fetch=async(url,options)=>{calls.push([url,options]);if(options.method==='POST')throw new w.TypeError('lost response');return{ok:true,json:async()=>({model,phase:'ready'})};};
 node.querySelector('.execution-apply').click();await flush();
 assert.match(node.textContent,/pending/);
 node.querySelector('.execution-apply').click();await flush();
 assert.equal(calls.length,2);assert.equal(calls[0][0],'/api/rlt/recover-runtime');assert.equal(calls[1][0],'/api/deployment/status');assert.equal(calls[1][1].method,undefined);
 dom.window.close();
});
test('unlabelled history can be labelled with version guard; deferred file cannot',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#labels'),ui=w.CobotHistoryLabels.create(node),calls=[];
 w.fetch=async(url,options={})=>{calls.push([url,options]);return{ok:true,json:async()=>({episode_uuid:'u',episode_outcome:options.method?'success':'unknown',keep_for_training:'false',label_updated_at:'v1',replay_modified:false})};};
 await ui.update({episode_uuid:'u',history_format:'rollout'},'/data');
 node.querySelector('.history-outcome').value='success';node.querySelector('.history-outcome').dispatchEvent(new w.Event('change'));await flush();
 const saved=JSON.parse(calls[1][1].body);assert.equal(saved.outcome,'success');assert.equal(saved.expected_label_updated_at,'v1');assert.equal(saved.keep_for_training,undefined);assert.equal(saved.operator_note,undefined);assert.equal(node.querySelector('input,button'),null);
 await ui.update({episode_uuid:'broken',history_format:'deferred'},'/data');assert.equal(node.querySelector('.history-outcome').disabled,true);assert.equal(node.querySelector('.history-outcome').value,'unknown');dom.window.close();
});

test('each model exposes independent Hz RTC filter controls with no explanation block',()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#options'),ui=w.CobotExecutionOptions.create(node);
 for(const kind of ['rlt','pi05','vla']){
  ui.update({id:kind,kind,control_hz:20},{});
  assert.equal(node.hidden,false);assert.equal(node.querySelector('.execution-summary'),null);
  const rtc=node.querySelector('.execution-rtc');rtc.checked=true;rtc.dispatchEvent(new w.Event('change'));
  assert.equal(ui.value.enabled,true);assert.equal(ui.value.rtc,true);assert.equal(ui.value.publish_hz,20);
  assert.equal(node.querySelector('.execution-hz').disabled,true);
 }
 dom.window.close();
});
test('lost label response checks GET without repeating PUT and retains selected episode',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#labels'),ui=w.CobotHistoryLabels.create(node),calls=[];
 let stored='unknown';w.fetch=async(url,options={})=>{calls.push([url,options.method]);if(options.method==='PUT'){stored='success';throw new w.TypeError('response lost');}return{ok:true,json:async()=>({episode_outcome:stored,label_updated_at:'v2'})};};
 await ui.update({episode_uuid:'u'},'/data');const select=node.querySelector('select');select.value='success';select.dispatchEvent(new w.Event('change'));await flush();await flush();
 assert.equal(select.value,'success');assert.equal(select.disabled,false);assert.deepEqual(calls.map(c=>c[1]),[undefined,'PUT',undefined]);dom.window.close();
});
test('late autosave response cannot replace newer episode selection',async()=>{
 const [dom,w]=setup(),node=w.document.querySelector('#labels'),ui=w.CobotHistoryLabels.create(node);
 let finish;w.fetch=async(url,options={})=>options.method==='PUT'?new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({episode_outcome:'success',label_updated_at:'v2'})});}):{ok:true,json:async()=>({episode_outcome:'unknown',label_updated_at:'v1'})};
 await ui.update({episode_uuid:'a'},'/data');const select=node.querySelector('select');select.value='success';select.dispatchEvent(new w.Event('change'));
 await ui.update({episode_uuid:'b'},'/other');finish();await flush();assert.equal(select.value,'unknown');assert.equal(select.disabled,false);dom.window.close();
});

test('selected publication50 is reflected in details and active20 remains explicit until applied',()=>{
 const [dom,w]=setup(),host=w.document.querySelector('#options');let changes=0;
 w.eval(fs.readFileSync('segmented_frontend/model_picker.js','utf8'));
 const ui=w.CobotExecutionOptions.create(host,{onChange:()=>changes++});
 const model={id:'m',kind:'rlt',execution_settings:{publish_hz:20,logical_hz:20,rtc:false,smoothing:false}};
 ui.update(model,{model,phase:'ready'});
 const custom=host.querySelector('.execution-custom'),hz=host.querySelector('.execution-hz');custom.checked=true;custom.dispatchEvent(new w.Event('change'));hz.value='50';hz.dispatchEvent(new w.Event('change'));
 const facts=w.document.createElement('dl');w.CobotModelPicker.renderDetails(facts,ui.describe());
 assert.match(facts.textContent,/Action publication rate50 Hz/);assert.match(facts.textContent,/Active action publication rate20 Hz/);assert.equal(changes,2);
 ui.update(model,{phase:'offline'});assert.equal(ui.describe().active_execution_settings,null);dom.window.close();
});
test('history starts from recorded result, then accepts authoritative GET and broadcasts root-bound changes',async()=>{
 const [dom,w]=setup(),host=w.document.querySelector('#labels'),ui=w.CobotHistoryLabels.create(host);let finish;const events=[];
 w.document.addEventListener('cobot:history-label-saved',event=>events.push(event.detail));
 w.fetch=()=>new Promise(resolve=>finish=()=>resolve({ok:true,json:async()=>({episode_outcome:'failure',label_updated_at:'v'})}));
 const pending=ui.update({episode_uuid:'u',episode_outcome:'success'},'/old');
 assert.equal(host.querySelector('select').value,'success');finish();await pending;
 assert.equal(host.querySelector('select').value,'failure');assert.equal(host.querySelector('select').disabled,false);assert.equal(events[0].data_root,'/old');dom.window.close();
});
