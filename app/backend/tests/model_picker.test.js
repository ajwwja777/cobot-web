const assert=require("node:assert/strict");
const test=require("node:test");
const fs=require("node:fs");
const {JSDOM}=require("jsdom");

function setup(saved){
  const dom=new JSDOM('<div id="picker"></div>',{url:"http://localhost",runScripts:"outside-only"});
  const w=dom.window;
  w.CobotPreferences={language:"zh"};
  for(const file of ["model_picker.js","collection_model_ui.js"])
    w.eval(fs.readFileSync("segmented_frontend/"+file,"utf8"));
  if(saved)w.localStorage.setItem("cobot-model-selection",JSON.stringify(saved));
  const select=w.document.createElement("select");select.id="model";
  const changes=[];
  const picker=w.CobotModelPicker.create({container:w.document.getElementById("picker"),modelSelect:select,sceneId:"scene",onChange:value=>changes.push(value)});
  const models=[
    {id:"plug",family:"RLT",task:"plug_insertion",stage1_step:4999,learner_step:5000,step:5000,stage:"warmup",actor_version:2500,checkpoint:"/weights/actor.pkl",available:true},
    {id:"pot",family:"π0.5",task:"in_the_pot",parent_step:2000,step:3000,checkpoint:"/weights/pi05",available:true},
    {id:"missing",family:"RLT",task:"plug_insertion",available:false,availability:"missing_files"}
  ];
  return {dom,w,select,picker,models,changes};
}
test("choosing a model from all scenes pairs its scene, retains weights path, and localizes both fields",()=>{
  const {dom,w,select,picker,models,changes}=setup();
  try{
    picker.update(models,"plug");
    const scene=w.document.getElementById("scene");
    scene.value="";scene.dispatchEvent(new w.Event("change"));
    assert.equal(select.options.length,2); // Steps are scoped to RLT.
    const family=w.document.getElementById("model-family");
    family.value="π0.5";family.dispatchEvent(new w.Event("change"));
    select.value="pot";select.dispatchEvent(new w.Event("change"));
    assert.equal(scene.value,"in_the_pot");
    assert.deepEqual([...select.options].map(o=>o.value),["","pot"]);
    assert.equal(select.selectedOptions[0].textContent,"3000");
    assert(!select.selectedOptions[0].textContent.includes("/weights/"));
    assert.match(w.document.getElementById("model-path").textContent,/\/weights\/pi05/);
    assert.equal(changes.at(-1).modelId,"pot");
    w.CobotPreferences.language="en";
    w.document.dispatchEvent(new w.Event("cobot:language"));
    assert.equal(scene.getAttribute("aria-label"),"Scene");
    assert.equal(select.getAttribute("aria-label"),"Version / mode");
    assert(!/[\u4e00-\u9fff]/u.test(w.document.getElementById("picker").textContent));
    picker.update([...models,{id:"other",task:"new_scene",family:"New model",step:123,available:true}],"plug");
    assert.equal(select.value,"pot"); // Heartbeats and catalog growth preserve the user's choice.
    assert([...scene.options].some(o=>o.value==="new_scene"));
  }finally{dom.window.close();}
});
test("a removed or unavailable saved model cannot become a new loadable selection",()=>{
  const {dom,picker,models,select}=setup({scene:"plug_insertion",model:"missing"});
  try{
    picker.update(models,"pot");
    assert.equal(select.value,"plug");
    assert.equal(select.querySelector('option[value="missing"]'),null);
    picker.select("missing");
    assert.equal(select.value,"plug");
    picker.update(models.filter(m=>m.id!=="plug"));
    assert.equal(select.value,"");
  }finally{dom.window.close();}
});


test("RLT picker shows published snapshot separately from learner and inference",()=>{
 const dom=new JSDOM('<dl id="facts"></dl>',{runScripts:"outside-only"}),w=dom.window;
 w.eval(fs.readFileSync("segmented_frontend/model_picker.js","utf8"));
 const model={family:"RLT",stage:"online",stage1_step:4999,available:true,
   publication_tracked:true,learner_step:6915,learner_actor_version:3457,
   published_learner_step:6500,published_actor_version:3250,actor_version:3250,
   last_inference_actor_version:3250,inference_episode_id:14,step:6500};
 assert.equal(w.CobotModelPicker.stepLabel(model),"6500");
 assert.doesNotMatch(w.CobotModelPicker.stepLabel(model),/6915/);
 const facts=w.document.querySelector("#facts");w.CobotModelPicker.renderDetails(facts,model);
 assert.match(facts.textContent,/Learner trained step6915/);
 assert.match(facts.textContent,/Published learner step6500/);
 assert.match(facts.textContent,/Last inference Actor3250/);
 assert.match(facts.textContent,/415 trained steps not yet published/);
 dom.window.close();
});


test('50 Hz publication is displayed separately from logical Replay20',()=>{
 const {dom,w}=setup();const details=w.document.createElement('dl');
 w.CobotModelPicker.renderDetails(details,{kind:'rlt',control_hz:20,publish_hz:50,
   execution_settings:{publish_hz:50,logical_hz:20,rtc:true,smoothing:false}});
 assert.match(details.textContent,/Action publication rate50 Hz/);
 assert.match(details.textContent,/Logical control \/ Replay rate20 Hz/);
 assert(!details.textContent.includes('Control rate'));dom.window.close();
});

test('same checkpoint frequency presets occupy a single numeric steps option',()=>{
 const {dom,w,picker,select}=setup();const rows=[{id:'base',kind:'rlt',family:'RLT',task:'plug',step:7000,checkpoint:'/same/actor.pkl',available:true},...[20,30,40,50].map(hz=>({id:'rtc'+hz,kind:'rlt',family:'RLT',task:'plug',step:7000,checkpoint:'/same/actor.pkl',execution_profile:'rtc'+hz,experiment_label:hz+' Hz',available:true}))];
 picker.update(rows,'base');assert.deepEqual([...select.options].map(o=>o.textContent),['Choose steps','7000 · 模式待核验']);
 picker.select('rtc50',{loaded:true});assert.equal(select.value,'rtc50');assert.deepEqual([...select.options].map(o=>o.textContent),['Choose steps','7000 · 模式待核验']);dom.window.close();
});

test('methods preserve original and MC30 steps while hiding unknown assets and reference duplicates',()=>{
 const {dom,w,picker,select}=setup();
 const common={kind:'rlt',family:'RLT',task:'plug',available:true};
 picker.update([{...common,id:'fixed',step:5000,checkpoint:'/fixed'},
 {...common,id:'ref',step:4999,stage:'stage1',checkpoint:'/stage1'},
 {...common,id:'junk',checkpoint:'/inventory'},
 {...common,id:'frozen',step:11500,stage:'frozen',checkpoint:'/online'},
 {...common,id:'online',step:11500,stage:'online',training_enabled:true,checkpoint:'/online'},
 {...common,id:'mc30',step:7480,runtime_profile:'credit_mc30',checkpoint:'/mc30'}],'fixed');
 assert.deepEqual([...select.options].map(o=>o.textContent),['Choose steps','5000 · 模式待核验','11500 · 冻结','11500 · Online']);
 const method=w.document.getElementById('model-method');
 assert.deepEqual([...method.options].map(o=>o.value),['original','mc30']);
 method.value='mc30';method.dispatchEvent(new w.Event('change'));
 assert.deepEqual([...select.options].map(o=>o.textContent),['Choose steps','7480 · 模式待核验']);
 select.value='mc30';select.dispatchEvent(new w.Event('change'));
 picker.update([{...common,id:'fixed',step:5000,checkpoint:'/fixed'}, {...common,id:'mc30',step:7500,runtime_profile:'credit_mc30',checkpoint:'/mc30'}]);
 assert.equal(select.value,'mc30');assert.equal(select.selectedOptions[0].textContent,'7500 · 模式待核验');dom.window.close();
});


test('same weights and step preserve frozen and Online IDs without changing launch selection',()=>{
 const {dom,w,picker,select,changes}=setup();
 const common={kind:'rlt',family:'RLT',task:'plug',step:7000,learner_step:7000,actor_version:3500,checkpoint:'/same/actor.pkl',available:true};
 picker.update([{...common,id:'frozen',mode:'frozen',training_enabled:false},{...common,id:'online',mode:'online',training_enabled:true}], 'frozen');
 assert.deepEqual([...select.options].map(o=>o.value),['','frozen','online']);
 assert.match(select.options[1].textContent,/冻结.*Actor 3500/);
 assert.match(select.options[2].textContent,/Online.*Actor 3500/);
 select.value='online';select.dispatchEvent(new w.Event('change'));
 assert.equal(changes.at(-1).modelId,'online');
 assert.match(w.document.getElementById('model-hint').textContent,/Online.*online.*Learner 7000/);
 picker.update([{...common,id:'frozen',mode:'frozen',training_enabled:false},{...common,id:'online',mode:'online',training_enabled:true}]);
 assert.equal(select.value,'online');dom.window.close();
});

test('loaded identity uses observed counters and cannot call evaluation Online learning',()=>{
 const {dom,w}=setup(); const api=w.CobotModelPicker;
 const model={kind:'rlt',id:'online',mode:'online',training_enabled:true,learner_step:7000,actor_version:3500};
 assert.match(api.runtimeIdentity({model,session:{evaluation_only:true,learner_version:7010,actor_version:3500}}),/冻结.*Learner 关闭.*Learner 7010.*最近推理 Actor 3500/);
 assert.match(api.runtimeIdentity({model,session:{evaluation_only:false,learner_version:7010,actor_version:3500}}),/Online.*Learner 已启用.*Learner 7010/);
 assert.match(api.runtimeIdentity({model,session:{}}),/登记 Learner 7000.*登记 Actor 3500.*推理未观测/);
 dom.window.close();
});
