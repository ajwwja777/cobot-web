const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup(failFirstRecords=false,extraEntries=[]){
 const dom=new JSDOM(fs.readFileSync('segmented_frontend/index.html','utf8'),{url:'http://localhost',runScripts:'outside-only'}),w=dom.window,calls=[];
 w.setInterval=()=>0;w.CobotPreferences={language:'zh'};
 let state={phase:'idle',active:false,default_data_root:'/data/motion'};
 const entries=[{id:'teleop-good.jsonl',name:'示教一',frames:20,valid:true},{id:'teleop-bad.jsonl',name:'中断',frames:2,valid:false},...extraEntries];
 w.fetch=async(url,options={})=>{
  if(url.startsWith("/api/motion/records")&&failFirstRecords){failFirstRecords=false;throw new w.TypeError("transient read failure");}
  if(options.body){const body=JSON.parse(options.body);calls.push({url,body});
   if(body.action==='rename')entries[0].name=body.name;
   else if(body.action==='delete')entries.splice(entries.findIndex(r=>r.id===body.trace_id),1);
   else if(body.action==='discard')state={...state,active:false,phase:'discarded'};
   else if(body.action==='stop')state={...state,active:false,phase:'completed'};
   else state={...state,trace_ids:body.trace_ids||[body.trace_id],action:body.action,id:'run1',active:true,phase:body.action==='record'?'recording':'replaying'};
  }
  return {ok:true,json:async()=>url.startsWith('/api/motion/records')?{records:entries,data_root:'/data/motion'}:state};
 };
 w.eval(fs.readFileSync('segmented_frontend/motion_ui.js','utf8'));await tick();await tick();
 return {dom,w,calls};
}
test('record is in collection; replay is in deployment; render makes no operation requests',async()=>{
 const {dom,w,calls}=await setup();try{
  assert.ok(w.document.querySelector('[data-page="operation"] #motion-record-panel'));
  assert.equal(w.document.querySelector("#motion-operation-trajectory").textContent,"轨迹录制");
  assert.equal(w.document.querySelector("#motion-deployment-trajectory").textContent,"轨迹回放");
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

test('top-level mode tabs reveal controls and switching never submits an operation',async()=>{
 const {dom,w,calls}=await setup();try{
  const page=w.document.querySelector('[data-page="deployment"]');
  assert.equal(page.firstElementChild.id,'motion-deployment-tabs');
  assert.ok(w.document.querySelector('#motion-deployment-workspace').classList.contains('motion-mode-hidden'));
  w.document.querySelector('#motion-deployment-trajectory').click();
  assert.equal(w.document.querySelector('#motion-deployment-trajectory').getAttribute('aria-selected'),'true');
  assert.ok(!w.document.querySelector('#motion-deployment-workspace').classList.contains('motion-mode-hidden'));
  assert.ok(w.document.querySelector('#motion-deployment-existing').classList.contains('motion-mode-hidden'));
  w.document.querySelector('#motion-deployment-normal').click();
  assert.ok(!w.document.querySelector('#motion-deployment-existing').classList.contains('motion-mode-hidden'));
  assert.deepEqual(calls,[]);
 }finally{dom.window.close();}
});
test('hidden model and episode controls receive no global robot shortcut',async()=>{
 const {dom,w,calls}=await setup();try{
  const page=w.document.querySelector('[data-page="deployment"]');
  w.document.querySelectorAll('[data-page]').forEach(n=>n.classList.remove('active'));page.classList.add('active');
  let modelShortcuts=0;w.document.addEventListener('keydown',()=>modelShortcuts++);
  w.document.querySelector('#motion-deployment-trajectory').click();
  for(const key of ['ArrowRight','ArrowLeft','ArrowUp','ArrowDown',' '])w.document.body.dispatchEvent(new w.KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
  assert.equal(modelShortcuts,0);assert.deepEqual(calls,[]);
  w.document.querySelector('#motion-deployment-normal').click();
  w.document.body.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
  assert.equal(modelShortcuts,1);
 }finally{dom.window.close();}
});

test('initial directory read failure remains editable and polling recovers the trace list',async()=>{
 const {dom,w,calls}=await setup(true);try{
  assert.equal(w.document.querySelector('#motion-record-root').disabled,false);
  assert.equal(w.document.querySelector('#motion-record').disabled,true);
  await w.CobotMotionUI.poll();
  assert.equal(w.document.querySelector('#motion-trace').options.length,2);
  assert.equal(w.document.querySelector('#motion-record').disabled,false);
  assert.deepEqual(calls,[]);
 }finally{dom.window.close();}
});

test('ordered queue supports add, reorder, remove and submits one sequence with one video',async()=>{
 const {dom,w,calls}=await setup(false,[{id:'teleop-two.jsonl',name:'示教二',frames:30,valid:true}]);try{
  const choose=id=>{const node=w.document.querySelector('#motion-trace');node.value=id;node.dispatchEvent(new w.Event('change'));};
  const add=w.document.querySelector('#motion-queue-add');add.click();choose('teleop-two.jsonl');add.click();
  let rows=w.document.querySelectorAll('#motion-queue li');assert.equal(rows.length,2);
  assert.match(rows[0].textContent,/示教一/);assert.match(rows[1].textContent,/示教二/);
  rows[1].querySelector('[data-queue-action="up"]').click();
  assert.match(w.document.querySelector('#motion-queue li').textContent,/示教二/);
  choose('teleop-bad.jsonl');assert.equal(add.disabled,true);add.click();assert.equal(w.document.querySelectorAll('#motion-queue li').length,2);
  const video=w.document.querySelector('#motion-keep-video');video.checked=true;w.document.querySelector('#motion-video-root').value='/videos';
  w.document.querySelector('#motion-replay').click();await tick();await tick();
  assert.deepEqual(calls[0].body,{action:'replay',data_root:'/data/motion',trace_ids:['teleop-two.jsonl','teleop-good.jsonl'],confirmed:true,video:{enabled:true,data_root:'/videos'}});
  assert.ok([...w.document.querySelectorAll('#motion-queue button')].every(n=>n.disabled));
  w.document.querySelector('#motion-replay-stop').click();await tick();await tick();
  assert.deepEqual(calls[1].body,{action:'stop',run_id:'run1'});
  w.document.querySelector('#motion-queue [data-queue-action="remove"]').click();
  assert.equal(w.document.querySelectorAll('#motion-queue li').length,1);
 }finally{dom.window.close();}
});
test('changing directory clears the old sequence without starting replay',async()=>{
 const {dom,w,calls}=await setup();try{
  w.document.querySelector('#motion-queue-add').click();assert.equal(w.document.querySelectorAll('#motion-queue li').length,1);
  const root=w.document.querySelector('#motion-replay-root');root.value='/new';root.dispatchEvent(new w.Event('change'));await tick();
  assert.equal(w.document.querySelectorAll('#motion-queue li').length,0);assert.deepEqual(calls,[]);
 }finally{dom.window.close();}
});

test('discard targets the running record; delete confirms and removes it from the queue',async()=>{
 const {dom,w,calls}=await setup();try{
  w.document.querySelector('#motion-record').click();await tick();await tick();
  assert.equal(w.document.querySelector('#motion-delete').disabled,true);
  w.document.querySelector('#motion-record-discard').click();await tick();await tick();
  assert.deepEqual(calls[1].body,{action:'discard',run_id:'run1'});
  assert.match(w.document.querySelector('#motion-record-status').textContent,/已放弃/);
  w.document.querySelector('#motion-queue-add').click();assert.equal(w.document.querySelectorAll('#motion-queue li').length,1);
  w.confirm=()=>false;w.document.querySelector('#motion-delete').click();await tick();assert.equal(calls.length,2);
  w.confirm=()=>true;w.document.querySelector('#motion-delete').click();await tick();await tick();
  assert.equal(calls[2].body.action,'delete');assert.equal(calls[2].body.trace_id,'teleop-good.jsonl');assert.equal(calls[2].body.confirmed,true);
  assert.equal(w.document.querySelectorAll('#motion-queue li').length,0);
 }finally{dom.window.close();}
});
