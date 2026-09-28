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
    {id:"plug-v3-warmup-5k", kind:"rlt", mode:"frozen", checkpoint:"/models/actor_snapshot.pkl", available:true},
    {id:"pi05-in-the-pot-dagger", kind:"pi05", checkpoint:"/models/pi05/step_3000", available:true}
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
  for(const file of ["console_ui.js","unified_collection.js","collection_model_ui.js"])
    w.eval(fs.readFileSync("segmented_frontend/"+file,"utf8"));
  w.CobotCollectionModel.mount(); w.CobotUnifiedCollection.mount();
  return {dom,w,requests,models,setState:value=>{state={...state,...value};}};
}

test("shared load uses one API, exposes paths, and keeps storage available while loading",async()=>{
  const {dom,w,requests,setState}=setup();
  try {
    await tick();await w.refreshConsole();
    const $=id=>w.document.getElementById(id);
    assert.match($("collection-model-select").selectedOptions[0].textContent,/\/models\/actor_snapshot.pkl/);
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
    assert.match($("deployment-model").selectedOptions[0].textContent,/\/models\/actor_snapshot.pkl/);
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
