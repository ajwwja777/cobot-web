const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const {JSDOM} = require("jsdom");
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup() {
  const dom = new JSDOM(fs.readFileSync("segmented_frontend/index.html", "utf8"),
    {url:"http://localhost", runScripts:"outside-only", pretendToBeVisual:true});
  const w = dom.window, requests = [];
  const models = [
    {id:"plug-v3-warmup-5k", family:"RLT", task:"plug_insertion", kind:"rlt", mode:"frozen", step:5000, checkpoint:"/models/actor_snapshot.pkl", available:true},
    {id:"pi05-in-the-pot-dagger", family:"π0.5", task:"in_the_pot", kind:"pi05", step:3000, checkpoint:"/models/pi05/step_3000", available:true}
  ];
  let state = {phase:"offline", models, data_root:"/data/evaluations", status_stale:false};
  let mode = "rlt";
  w.setInterval = () => 0;
  w.CobotPreferences = {language:"en", text:value=>value};
  w.CobotPathPicker = {create:()=>({setDisabled(){}, refresh(){}, remember(){}})};
  w.CobotWorkspaceUI = {report(){}, sessionStatus(){}};
  w.collectionIsRlt = () => mode === "rlt";
  w.updateButtons = () => {};
  w.chooseCollection = async value => {mode=value;};
  w.refreshConsole = async () => w.CobotUnifiedCollection.render({
    console:{selected_mode:mode, rlt_backend_phase:"ready_disarmed"},
    session:{phase:"disarmed"}, capture:{capture_state:"idle"}
  });
  w.fetch = async (path, options={}) => {
    let payload=state;
    if(options.body) {
      const body=JSON.parse(options.body); requests.push({path,body});
      if(body.action==="load")state={...state,phase:"loading",model:models.find(m=>m.id===body.model_id),started_at:1};
      if(body.action==="session_start")state={...state,session_active:true};
      payload=state;
    } else if(path.startsWith("/api/deployment/records"))payload={records:[],total:0,success:0,failure:0,aborted:0};
    else if(path==="/api/console/devices")payload={home_poses:{}};
    return {ok:true,status:200,headers:{get:()=>"application/json"},text:async()=>JSON.stringify(payload),json:async()=>payload};
  };
  w.localStorage.setItem("cobot-capture-use-model","true");
  // No saved model and no legacy RLT catalog: the shared catalog must suffice.
  for(const file of ["console_ui.js","model_picker.js","recorder_recovery.js","unified_collection.js","collection_model_ui.js"])
    w.eval(fs.readFileSync("segmented_frontend/"+file,"utf8"));
  w.CobotCollectionModel.mount(); w.CobotUnifiedCollection.mount();
  return {dom,w,requests,models,setState:value=>{state={...state,...value};}};
}

test("shared load uses one API, exposes paths, and keeps storage available while loading",async()=>{
  const {dom,w,requests,setState}=setup();
  try {
    await tick();await w.refreshConsole();
    const $=id=>w.document.getElementById(id);
    assert.match($("collection-model-select-path").textContent,/\/models\/actor_snapshot.pkl/);
    assert.equal($("collection-load").disabled,false);
    $("collection-load").click();await tick();await tick();
    assert.deepEqual(requests.map(r=>r.path),["/api/collection/model"]);
    assert.equal(requests[0].body.action,"load");
    assert.equal($("collection-data-root").disabled,false);
    assert.equal($("collection-storage-use").disabled,false);
    assert.equal($("collection-load").disabled,true);
    assert.equal($("collection-unload").disabled,false);
    setState({phase:"ready"});
    await w.CobotCollectionModel.refresh();
    assert.equal($("collection-session-start").disabled,false);
    $("collection-session-start").click();await tick();
    assert.equal(requests.at(-1).body.action,"session_start");
    assert.equal($("collection-session-start").disabled,true);
    assert.equal($("collection-session-stop").disabled,false);
  } finally {dom.window.close();}
});

test("deployment and collection reflect the same loaded model; typed directory survives polling",async()=>{
  const {dom,w,models,setState}=setup();
  try {
    await tick();await w.refreshConsole();
    setState({phase:"ready",model:models[0],started_at:2});
    await w.CobotCollectionModel.refresh();
    w.eval(fs.readFileSync("segmented_frontend/deployment_ui.js","utf8"));
    await tick();
    const $=id=>w.document.getElementById(id);
    assert.equal($("deployment-model").value,models[0].id);
    assert.equal($("deploy-load").disabled,true);
    assert.match($("deployment-model-path").textContent,/\/models\/actor_snapshot.pkl/);
    $("deployment-storage").value="/data/my-next-evaluation";
    $("deployment-storage").dispatchEvent(new w.Event("input"));
    await w.CobotDeploymentUI.poll();
    assert.equal($("deployment-storage").value,"/data/my-next-evaluation");
    setState({phase:"loading",model:models[1],started_at:3});
    await w.CobotCollectionModel.refresh();
    assert.equal($("collection-model-select").value,models[1].id);
    assert.equal($("collection-data-root").disabled,false);
  } finally {dom.window.close();}
});

test("backend error detail survives HTTP 503",async()=>{
  const {dom,w}=setup();
  try {
    const response={ok:false,status:503,headers:{get:()=>"application/json"},
      text:async()=>JSON.stringify({error:"Task5 finalization failed: operator_nodes"})};
    await assert.rejects(w.CobotConsoleUI.parseApiResponse(response),/Task5 finalization failed: operator_nodes/);
  } finally {await tick();dom.window.close();}
});

test("collection and deployment share scene filtering, disabled options and selection without loading",async()=>{
  const {dom,w,requests,models,setState}=setup();
  try {
    models.push(
      {id:"missing-plug",kind:"rlt",family:"RLT",task:"plug_insertion",step:6000,available:false,availability:"missing_files",checkpoint:"/missing/actor.pkl"},
      {id:"cli-book",kind:"vla",family:"π0.5",task:"lift_book",available:false,availability:"cli_only",checkpoint:"/models/book"},
      {id:"no-load",kind:"external",family:"External",task:"plug_insertion",available:true,capabilities:{load:false},checkpoint:"/models/no-load"}
    );
    await tick();await w.refreshConsole();
    w.eval(fs.readFileSync("segmented_frontend/deployment_ui.js","utf8"));
    await tick();
    const $=id=>w.document.getElementById(id);
    for(const id of ["collection-model-select","deployment-model"]){
      assert.equal($(id).querySelector('option[value="missing-plug"]').disabled,true);
      assert.equal($(id).querySelector('option[value="no-load"]'),null); // Different family.
      assert(![...$(id+"-family").options].some(o=>o.value==="External"));
      assert.equal([...$(id).options].some(o=>o.value==="pi05-in-the-pot-dagger"),false);
      assert(!$(id).selectedOptions[0].textContent.includes("/models/"));
    }
    $("deployment-scene").value="in_the_pot";
    $("deployment-scene").dispatchEvent(new w.Event("change"));
    assert.equal($("deployment-model").value,"");
    assert.equal($("deploy-load").disabled,true);
    assert.equal($("collection-scene-select").value,"in_the_pot");
    assert.equal([...$("deployment-model").options].some(o=>o.value==="plug-v3-warmup-5k"),false);
    $("deployment-model").value=models[1].id;
    $("deployment-model").dispatchEvent(new w.Event("change"));
    await tick();
    assert.equal($("collection-model-select").value,models[1].id);
    assert.equal($("collection-scene-select").value,"in_the_pot");
    assert.match($("collection-model-select-path").textContent,/step_3000/);
    assert.equal(requests.length,0); // Filtering/selecting never loads or starts a Session.
    $("deployment-scene").value="plug_insertion";
    $("deployment-scene").dispatchEvent(new w.Event("change"));
    assert(![...$("deployment-model-family").options].some(o=>o.value==='External'));

    $("deployment-scene").value="lift_book";
    $("deployment-scene").dispatchEvent(new w.Event("change"));
    assert.equal($("deployment-model").value,"");
    assert.equal($("deployment-model").querySelector('option[value="cli-book"]'),null);
    assert.equal($("deploy-load").disabled,true);
    assert.equal($("collection-load").disabled,true);
    $("deployment-model").value="cli-book";
    $("deployment-model").dispatchEvent(new w.Event("change"));
    assert.equal($("deployment-model").value,""); // Defensive rejection of a synthetic disabled selection.
    assert.equal(requests.length,0);
    setState({phase:"ready",model:models[0],started_at:5,active:{id:"trial"}});
    await w.CobotDeploymentUI.poll();
    assert.equal($("deployment-scene").value,"plug_insertion");
    assert.equal($("deployment-model").value,models[0].id);
    assert.equal($("deployment-scene").disabled,true);
    assert.equal($("deployment-model").disabled,true);
  } finally {await tick();dom.window.close();}
});

test("explicit model selection applies registered collection directory, not a weight-derived guess",async()=>{
 const {dom,w,models}=setup();
 try{
  models[0].data_directories={collection:"/data/datasets/plug/warmup"};
  const used=[];w.saveRltStorage=async()=>used.push(w.document.getElementById("rlt-data-root").value);
  await tick();await w.refreshConsole();
  const picker=w.document.getElementById("collection-model-select");
  picker.value=models[0].id;picker.dispatchEvent(new w.Event("change"));
  await tick();await tick();
  assert.deepEqual(used,["/data/datasets/plug/warmup"]);
  assert.equal(w.document.getElementById("collection-data-root").value,"/data/datasets/plug/warmup");
 }finally{dom.window.close();}
});


test("collection and deployment show runtime fault separately from recording recovery",async()=>{
 const {dom,w,models,setState,requests}=setup();
 try{
  await tick();await w.refreshConsole();
  const failure={code:"rtc_delay_exceeded",cause:"actual delay exceeded predicted delay",stage1_retained:true,recoverable:true};
  setState({phase:"error",model:models[0],started_at:2,runtime_failure:failure});
  await w.CobotCollectionModel.refresh();
  w.eval(fs.readFileSync("segmented_frontend/deployment_ui.js","utf8"));await tick();
  const blocks=[...w.document.querySelectorAll(".runtime-recovery")];
  assert.equal(blocks.length,2);
  for(const block of blocks){
   assert.equal(block.hidden,false);assert.match(block.textContent,/RTC execution timed out/);
   assert.equal(block.querySelector(".runtime-recover").disabled,false);
  }
  assert.equal(w.document.querySelector(".recorder-recovery").hidden,true);
  assert.match(w.document.getElementById("collection-model-state").textContent,/Stage1 weights remain loaded/);
  assert.match(w.document.getElementById("deployment-load-state").textContent,/RTC execution timed out/);
  assert.equal(w.document.getElementById("deploy-start").disabled,true);
  assert.equal(w.document.getElementById("deploy-load").disabled,false);
  assert.equal(requests.length,0);
 }finally{await tick();dom.window.close();}
});
