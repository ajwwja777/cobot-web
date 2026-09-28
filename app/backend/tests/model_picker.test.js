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
    assert.equal(select.options.length,4);
    select.value="pot";select.dispatchEvent(new w.Event("change"));
    assert.equal(scene.value,"in_the_pot");
    assert.deepEqual([...select.options].map(o=>o.value),["","pot"]);
    assert.match(select.selectedOptions[0].textContent,/DAgger 2000\+3000/);
    assert(!select.selectedOptions[0].textContent.includes("/weights/"));
    assert.match(w.document.getElementById("model-path").textContent,/\/weights\/pi05/);
    assert.equal(changes.at(-1).modelId,"pot");
    w.CobotPreferences.language="en";
    w.document.dispatchEvent(new w.Event("cobot:language"));
    assert.equal(scene.getAttribute("aria-label"),"Scene");
    assert.equal(select.getAttribute("aria-label"),"Model");
    assert(!/[\u4e00-\u9fff]/u.test(w.document.getElementById("picker").textContent));
    picker.update([...models,{id:"other",task:"new_scene",family:"New model",available:true}],"plug");
    assert.equal(select.value,"pot"); // Heartbeats and catalog growth preserve the user's choice.
    assert([...scene.options].some(o=>o.value==="new_scene"));
  }finally{dom.window.close();}
});
test("a removed or unavailable saved model cannot become a new loadable selection",()=>{
  const {dom,picker,models,select}=setup({scene:"plug_insertion",model:"missing"});
  try{
    picker.update(models,"pot");
    assert.equal(select.value,"plug");
    assert.equal(select.querySelector('option[value="missing"]').disabled,true);
    picker.select("missing");
    assert.equal(select.value,"plug");
    picker.update(models.filter(m=>m.id!=="plug"));
    assert.equal(select.value,"");
  }finally{dom.window.close();}
});
