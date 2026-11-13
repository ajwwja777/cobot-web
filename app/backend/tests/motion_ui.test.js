const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup(){
 const dom=new JSDOM(fs.readFileSync('segmented_frontend/index.html','utf8'),{url:'http://localhost',runScripts:'outside-only'}),w=dom.window,calls=[];
 w.setInterval=()=>0;w.CobotPreferences={language:'zh'};
 let state={phase:'idle',active:false,default_data_root:'/data/motion'};
 const entries=[{id:'teleop-good.jsonl',name:'示教一',frames:20,valid:true},{id:'teleop-bad.jsonl',name:'中断',frames:2,valid:false}];
 w.fetch=async(url,options={})=>{
  if(options.body){const body=JSON.parse(options.body);calls.push({url,body});
   if(body.action==='rename')entries[0].name=body.name;
   else if(body.action==='stop')state={...state,active:false,phase:'completed'};
   else state={...state,action:body.action,id:'run1',active:true,phase:body.action==='record'?'recording':'replaying'};
  }
  return {ok:true,json:async()=>url.startsWith('/api/motion/records')?{records:entries,data_root:'/data/motion'}:state};
 };
 w.eval(fs.readFileSync('segmented_frontend/motion_ui.js','utf8'));await tick();await tick();
 return {dom,w,calls};
}
test('record is in collection; replay is in deployment; render makes no operation requests',async()=>{
 const {dom,w,calls}=await setup();try{
  assert.ok(w.document.querySelector('[data-page="operation"] #motion-record-panel'));
  assert.ok(w.document.querySelector('[data-page="deployment"] #motion-replay-panel'));
  assert.deepEqual(calls,[]);assert.equal(w.CobotMotionUI.videoOptions(),null);
  assert.equal(w.document.querySelector('#motion-replay').disabled,false);
 }finally{dom.window.close();}
});
test('named record, identity-bound save, and incomplete trace replay blocked',async()=>{
 const {dom,w,calls}=await setup();try{
  w.document.querySelector('#motion-name').value='插孔轨迹';w.document.querySelector('#motion-record').click();await tick();await tick();
  assert.equal(calls[0].body.name,'插孔轨迹');assert.equal(calls[0].body.confirmed,true);
  assert.equal(w.document.querySelector('#motion-replay').disabled,true);
  w.document.querySelector('#motion-record-stop').click();await tick();await tick();
  assert.deepEqual(calls[1].body,{action:'stop',run_id:'run1'});
  const select=w.document.querySelector('#motion-trace');select.value='teleop-bad.jsonl';select.dispatchEvent(new w.Event('change'));
  assert.equal(w.document.querySelector('#motion-replay').disabled,true);
 }finally{dom.window.close();}
});
test('rename uses display metadata and replay carries selected video directory',async()=>{
 const {dom,w,calls}=await setup();try{
  w.document.querySelector('#motion-rename').value='新名称';w.document.querySelector('#motion-save-name').click();await tick();await tick();
  assert.equal(calls[0].body.action,'rename');assert.equal(calls[0].body.trace_id,'teleop-good.jsonl');
  const toggle=w.document.querySelector('#motion-keep-video');toggle.checked=true;toggle.dispatchEvent(new w.Event('change'));
  w.document.querySelector('#motion-video-root').value='/data/videos/custom';w.document.querySelector('#motion-replay').click();await tick();await tick();
  assert.deepEqual(calls[1].body.video,{enabled:true,data_root:'/data/videos/custom'});
 }finally{dom.window.close();}
});
test('deployment video opt-in and state disables changing a running archive',async()=>{
 const {dom,w}=await setup();try{
  const toggle=w.document.querySelector('#deploy-keep-video');toggle.checked=true;toggle.dispatchEvent(new w.Event('change'));
  assert.equal(w.document.querySelector('#deploy-video-options').hidden,false);
  w.document.querySelector('#deploy-video-root').value='/data/chosen';
  assert.deepEqual(JSON.parse(JSON.stringify(w.CobotMotionUI.videoOptions())),{enabled:true,data_root:'/data/chosen'});
  w.CobotDeploymentUI={state:{active:{id:'trial'},video:{state:'recording',directory:'/data/chosen/trial'}}};await w.CobotMotionUI.poll();
  assert.equal(toggle.disabled,true);assert.match(w.document.querySelector('#deploy-video-status').textContent,/chosen/);
 }finally{dom.window.close();}
});
