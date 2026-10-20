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


test('both loaded status panels show actual Frozen identity and keep Online selectable',async()=>{
 const {dom,w,models,setState,requests}=setup();
 try {
  models[0].learner_step=7000;models[0].actor_version=3500;models[0].training_enabled=false;
  models.push({...models[0],id:'plug-online',mode:'online',training_enabled:true});
  await tick();await w.refreshConsole();
  setState({phase:'ready',model:models[0],started_at:2,session:{evaluation_only:true,actor_version:3500,learner_version:7000}});
  await w.CobotCollectionModel.refresh();
  w.eval(fs.readFileSync('segmented_frontend/deployment_ui.js','utf8'));await tick();
  for(const id of ['collection-model-select','deployment-model']) {
   assert([...w.document.getElementById(id).options].some(o=>o.value==='plug-online'));
   assert.match(w.document.getElementById(id).selectedOptions[0].textContent,/Frozen/);
  }
  for(const id of ['collection-model-state','deployment-load-state'])
   assert.match(w.document.getElementById(id).textContent,/Loaded: Frozen.*Learner disabled.*Learner 7000.*Last inference Actor 3500/);
  assert.equal(requests.length,0);
 }finally{await tick();dom.window.close();}
});


test('released same model with historical PID can be loaded again',async()=>{
 const {dom,w,models,setState,requests}=setup();
 try {
  await tick();await w.refreshConsole();
  setState({phase:'offline',model:models[0],pid:2054513,process_started:false,model_ready:false,operation:null,session_active:false,session:null});
  await w.CobotCollectionModel.refresh();await w.refreshConsole();
  const button=w.document.getElementById('collection-load');
  assert.equal(button.disabled,false);
  button.click();await tick();await tick();
  assert.equal(requests.length,1);
  assert.equal(requests[0].body.action,'load');
  assert.equal(requests[0].body.model_id,models[0].id);
 }finally{await tick();dom.window.close();}
});

test('release must be verified before unlocking load; live and stale states stay blocked',async()=>{
 const {dom,w,models,setState,requests}=setup();
 try {
  await tick();await w.refreshConsole();
  for(const status of [
   {phase:'offline',process_started:false,status_stale:true},
   {phase:'offline',process_started:true,status_stale:false},
   {phase:'loading',process_started:true,status_stale:false},
   {phase:'paused',process_started:true,status_stale:false},
   {phase:'error',process_started:true,status_stale:false}
  ]){
   setState({...status,model:models[0],pid:2054513,operation:null});
   await w.CobotCollectionModel.refresh();await w.refreshConsole();
   assert.equal(w.document.getElementById('collection-load').disabled,true,JSON.stringify(status));
  }
  assert.equal(requests.length,0);
 }finally{await tick();dom.window.close();}
});


test("model-owned collection directory auto-matches on reload and updates history once",async()=>{
 const {dom,w,models,setState}=setup();
 const used=[],events=[];
 w.request=async(path,options)=>{const body=JSON.parse(options.body);used.push({path,...body});return body;};
 w.document.addEventListener('cobot:storage-used',e=>events.push(e.detail.path));
 try{
  models[0].collection_directory_policy='model';models[0].data_directories={collection:'/data/dedicated'};
  setState({phase:'ready',model:models[0],started_at:3});
  await tick();await w.refreshConsole();await tick();
  assert.equal(used.length,1);assert.equal(used[0].data_root,'/data/dedicated');
  assert.deepEqual(events,['/data/dedicated']);
  assert.equal(w.document.getElementById('collection-data-root').value,'/data/dedicated');
  assert.equal(w.document.getElementById('collection-data-root').disabled,true);
  assert.equal(w.document.getElementById('collection-storage-use').disabled,true);
  await w.refreshConsole();await tick();assert.equal(used.length,1);
  w.document.getElementById('rlt-data-root').value='/data/test'; // Late legacy refresh response.
  await w.refreshConsole();await tick();assert.equal(used.length,2);
  assert.equal(w.document.getElementById('rlt-data-root').value,'/data/dedicated');
 }finally{dom.window.close();}
});

test("model-owned directory waits for the active episode to end",async()=>{
 const {dom,w,models}=setup();let calls=0;
 w.request=async(path,options)=>{calls++;return JSON.parse(options.body);};
 try{
  await tick();await w.refreshConsole();
  w.CobotUnifiedCollection.render({session:{phase:'rollout'}});
  models[0].collection_directory_policy='model';models[0].data_directories={collection:'/data/dedicated'};
  await w.CobotCollectionModel.refresh();await tick();assert.equal(calls,0);
  w.CobotUnifiedCollection.render({session:{phase:'disarmed'}});await tick();assert.equal(calls,1);
 }finally{dom.window.close();}
});

test("directory rejection blocks start and does not report a false saved path or retry storm",async()=>{
 const {dom,w,models}=setup();let calls=0;
 w.request=async()=>{calls++;throw Error('disk_not_ready');};
 try{
  await tick();await w.refreshConsole();
  models[0].collection_directory_policy='model';models[0].data_directories={collection:'/data/dedicated'};
  await w.CobotCollectionModel.refresh();await tick();
  assert.equal(w.document.getElementById('collection-start').disabled,true);
  assert.match(w.document.getElementById('collection-storage-policy').textContent,/disk_not_ready/);
  await w.refreshConsole();await tick();assert.equal(calls,1);
 }finally{dom.window.close();}
});


test("EXPO online shows versions and gates the next episode while learning",async()=>{
  const {dom,w,models,setState}=setup();
  try {
    const model={id:"expo-online",family:"EXPO",kind:"vla",runtime_adapter:"expo_online",task:"plug_left_2cam",mode:"online",step:2000,checkpoint:"/expo/seed",available:true};
    models.push(model);await tick();await w.chooseCollection("normal");await w.refreshConsole();
    setState({phase:"paused",model,started_at:9,online:{phase:"ready",learner_step:4,actor_version:4,replay_steps:160}});
    await w.CobotCollectionModel.refresh();
    const $=id=>w.document.getElementById(id);
    assert.equal($("collection-model-select").value,model.id);
    assert.match($("collection-model-state").textContent,/EXPO Online.*Learner 4.*Actor 4.*Replay 160/);
    assert.equal($("collection-label-results").checked,true);
    assert.equal($("collection-label-results").disabled,true);
    assert.equal(w.CobotCollectionModel.ready(),true);
    setState({online:{phase:"updating",learner_step:5,actor_version:5,replay_steps:200}});
    await w.CobotCollectionModel.refresh();
    assert.equal(w.CobotCollectionModel.ready(),false);
    assert.equal($("collection-session-start").disabled,true);
    assert.equal($("collection-unload").disabled,true);
    $("start").disabled=false;w.CobotUnifiedCollection.render();
    assert.equal($("collection-start").disabled,true);
    assert.match($("collection-model-state").textContent,/Learning and saving/);
    setState({online:{phase:"publishing",learner_step:5,actor_version:5,served_actor_version:4,replay_steps:200,compute_host:"Learner A6000 / Actor Cobot"}});
    await w.CobotCollectionModel.refresh();
    assert.equal(w.CobotCollectionModel.ready(),false);
    assert.equal($("collection-start").disabled,true);
    assert.match($("collection-model-state").textContent,/Publishing local Actor.*Actor 5.*Local version 4.*Learner A6000/);

  } finally {dom.window.close();}
});


test("EXPO managed normal storage unlocks start only after confirmation and keeps counters after translation",async()=>{
 const {dom,w,models,setState}=setup();
 try {
  const model={id:"expo-managed",family:"EXPO",kind:"vla",runtime_adapter:"expo_online",task:"plug_left_2cam",mode:"online",step:2000,available:true,collection_directory_policy:"model",data_directories:{collection:"/data/expo-online"}};
  models.push(model);
  const calls=[];let complete;
  w.prepareStorage=async()=>{calls.push(w.document.querySelector('#capture-form input[name=data_root]').value);return new Promise(resolve=>complete=resolve);};
  await tick();await w.chooseCollection("normal");await w.refreshConsole();
  setState({phase:"paused",model,started_at:10,session_active:true,online:{phase:"ready",learner_step:8,actor_version:8,actor_updates:3,replay_steps:200,recipe:"native_sync",critic_updates:160,seed_episodes:25,seed_steps:3301,online_steps:200,completed_episodes:9,warmup_remaining:1,episodes_per_update:5,batch_episodes_collected:3,pending_update_calls:15}});
  await w.CobotCollectionModel.refresh();
  const $=id=>w.document.getElementById(id);
  $("start").disabled=false;w.CobotUnifiedCollection.render();
  assert.deepEqual(calls,["/data/expo-online"]);
  assert.equal($("collection-start").disabled,true);
  complete({data_root:"/data/expo-online"});await tick();
  assert.equal($("collection-start").disabled,false);
  // Load the real preference implementation: applying a language used to erase counters.
  w.eval(fs.readFileSync('segmented_frontend/preferences.js','utf8'));
  w.CobotPreferences.setLanguage('en');
  assert.match($("collection-model-state").textContent,/Learner 8.*Actor 8.*VLA.*3.*Replay 200/);
  assert.match($("collection-model-state").textContent,/Critic 160.*Seed 25 episodes.*3301 frames.*Online transitions 200.*Collected 9 episodes.*Warmup remaining 1/);
  assert.match($("collection-model-state").textContent,/Batch 3\/5 episodes.*Pending update calls 15/);
  w.CobotPreferences.setLanguage('zh');
  assert.match($("collection-model-state").textContent,/本批 3\/5 回合.*待执行更新组 15/);
  assert.match($("collection-model-state").textContent,/Learner 8.*Actor 8.*VLA.*3.*Replay 200/);
  assert.match($("collection-model-state").textContent,/Critic 160.*初始示教 25 回合.*3301 帧.*在线步数 200.*已采集 9 回合.*热身剩余 1/);
  await w.refreshConsole();await tick();assert.equal(calls.length,1);
  // A late old directory response must be rechecked, not silently accepted.
  w.document.querySelector('#capture-form input[name=data_root]').value='/data/old';
  w.CobotUnifiedCollection.render();assert.equal($("collection-start").disabled,true);
  assert.equal(calls.length,2);
  complete({data_root:"/data/wrong"});await tick();
  assert.equal($("collection-start").disabled,true);
  assert.match($("collection-storage-policy").textContent,/directory_mismatch/);
 } finally {await tick();dom.window.close();}
});


test("EXPO committed episodes unlock the next start through real button rendering",async()=>{
 const {dom,w,models,setState}=setup();
 try {
  const model={id:'expo-cycle',family:'EXPO',kind:'vla',runtime_adapter:'expo_online',task:'plug_left_2cam',step:2000,available:true,collection_directory_policy:'model',data_directories:{collection:'/data/expo-cycle'}};
  models.push(model);w.prepareStorage=async()=>({data_root:'/data/expo-cycle'});
  await tick();await w.chooseCollection('normal');await w.refreshConsole();
  setState({phase:'paused',model,started_at:11,session_active:true,online:{phase:'ready'}});
  await w.CobotCollectionModel.refresh();await tick();
  const source=fs.readFileSync('segmented_frontend/app.js','utf8');
  Object.assign(w,{$:s=>w.document.querySelector(s),$$:s=>w.document.querySelectorAll(s),
   captureHomeBusy:false,manualHomeBusy:false,storagePrepared:true,normalPathPicker:null,rltSession:null,rltOperationBusy:false,
   consoleStatus:{selected_mode:'normal',active_mode:null},updateManualHomeControls(){},updateEpisodeBrowserControls(){},hideDirectorySuggestions(){}});
  w.eval(source.slice(source.indexOf('function updateButtons()'),source.indexOf('async function refreshCaptureHomePoses()')));
  for(let n=1;n<=2;n++){
   w.current={episode_uuid:'episode-'+n,capture_state:'paused',buttons:{stop:true}};
   setState({phase:'running',online:{phase:'rollout'}});await w.CobotCollectionModel.refresh();w.updateButtons();
   assert.equal(w.document.getElementById('collection-start').disabled,true);
   w.current={episode_uuid:'episode-'+n,capture_state:'committed',buttons:{stop:false}};
   w.captureHomeBusy=true;setState({phase:'paused',online:{phase:'ready'}});await w.CobotCollectionModel.refresh();
   assert.equal(w.document.getElementById('collection-start').disabled,true);
   w.captureHomeBusy=false;w.updateButtons();
   assert.equal(w.document.getElementById('start').disabled,false);
   assert.equal(w.document.getElementById('collection-start').disabled,false);
  }
 }finally{dom.window.close();}
});


test('model directory waits for profile defaults to finish initializing',async()=>{
 const {dom,w,models,setState}=setup();
 try{
  w.CobotCaptureProfileReady=false;
  const model={id:'expo-init',family:'EXPO',kind:'vla',runtime_adapter:'expo_online',task:'plug_left_2cam',step:2000,available:true,collection_directory_policy:'model',data_directories:{collection:'/data/expo-init'}};
  models.push(model);let calls=0;
  w.prepareStorage=async()=>{calls++;return {data_root:w.document.querySelector('#capture-form input[name=data_root]').value};};
  await tick();await w.chooseCollection('normal');await w.refreshConsole();
  setState({phase:'paused',model,started_at:12,online:{phase:'ready'},session_active:true});
  await w.CobotCollectionModel.refresh();await tick();assert.equal(calls,0);
  w.document.querySelector('#capture-form input[name=data_root]').value='/data/test';
  w.CobotCaptureProfileReady=true;w.CobotUnifiedCollection.render();await tick();
  assert.equal(calls,1);assert.equal(w.document.querySelector('#capture-form input[name=data_root]').value,'/data/expo-init');
  assert.doesNotMatch(w.document.querySelector('#collection-storage-policy').textContent,/mismatch/);
 }finally{dom.window.close();}
});


test("async EXPO keeps collection enabled during learning and closes at the bounded queue",async()=>{
 const {dom,w,models,setState}=setup();
 try {
  const model={id:'expo-async',family:'EXPO',kind:'vla',runtime_adapter:'expo_online',task:'plug_left_2cam',step:2000,available:true,collection_directory_policy:'model',data_directories:{collection:'/data/expo-async'}};
  models.push(model);w.prepareStorage=async()=>({data_root:'/data/expo-async'});
  await tick();await w.chooseCollection('normal');await w.refreshConsole();
  const online={async_mode:'bounded_async_v1',phase:'ready',ready:true,learner_phase:'updating',learner_step:26,actor_version:25,published_actor_version:25,downloaded_actor_version:20,served_actor_version:20,pending_episodes:4,async_max_pending:10};
  setState({phase:'paused',model,started_at:14,session_active:true,online});
  await w.CobotCollectionModel.refresh();await tick();
  const $=id=>w.document.getElementById(id);$('start').disabled=false;w.CobotUnifiedCollection.render();
  assert.equal($('collection-start').disabled,false);
  assert.match($('collection-model-state').textContent,/Async learner updating.*Published 25.*Downloaded 20.*Served 20/);
  setState({online:{...online,phase:'backlog',ready:false,pending_episodes:10,collection_waiting_for:'backlog'}});
  await w.CobotCollectionModel.refresh();w.CobotUnifiedCollection.render();
  assert.equal($('collection-start').disabled,true);
  assert.match($('collection-model-state').textContent,/Wait for learner backlog/);
 } finally {dom.window.close();}
});


test("native continuous EXPO reports independent learner progress and keeps real start buttons ready",async()=>{
 const {dom,w,models,setState}=setup();
 try {
  const model={id:'expo-continuous',family:'EXPO',kind:'vla',runtime_adapter:'expo_online',task:'plug_left_2cam',step:2000,available:true,collection_directory_policy:'model',data_directories:{collection:'/data/expo-continuous'}};
  models.push(model);w.prepareStorage=async()=>({data_root:'/data/expo-continuous'});
  await tick();await w.chooseCollection('normal');await w.refreshConsole();
  const online={async_mode:'native_async_v1',learning_schedule:'native_continuous',phase:'ready',ready:true,learner_phase:'updating',recipe:'native_sync',learner_step:26,critic_updates:520,actor_updates:26,replay_steps:6000,replay_online_episodes:16,completed_episodes:17,pending_upload_episodes:2,pending_episodes:1,published_actor_version:25,downloaded_actor_version:24,served_actor_version:20,episodes_per_update:5,warmup_remaining:10,ep_timeout_secs:120};
  setState({phase:'paused',model,started_at:15,session_active:true,online});
  await w.CobotCollectionModel.refresh();await tick();
  const source=fs.readFileSync('segmented_frontend/app.js','utf8');
  Object.assign(w,{$:s=>w.document.querySelector(s),$$:s=>w.document.querySelectorAll(s),
   captureHomeBusy:false,manualHomeBusy:false,storagePrepared:true,normalPathPicker:null,rltSession:null,rltOperationBusy:false,
   consoleStatus:{selected_mode:'normal',active_mode:null},current:{capture_state:'committed',buttons:{}},updateManualHomeControls(){},updateEpisodeBrowserControls(){},hideDirectorySuggestions(){}});
  w.eval(source.slice(source.indexOf('function updateButtons()'),source.indexOf('async function refreshCaptureHomePoses()')));
  w.updateButtons();
  const $=id=>w.document.getElementById(id);
  assert.equal($('start').disabled,false);assert.equal($('collection-start').disabled,false);
  assert.match($('collection-model-state').textContent,/Continuous learning.*Training update \(including compilation \/ parameter sync\).*Learner 26.*Critic 520.*VLA updates 26/);
  assert.match($('collection-model-state').textContent,/Replay 6000.*Inserted episodes 16.*Collected 17.*Upload queue 2.*Awaiting Replay insertion 1/);
  assert.match($('collection-model-state').textContent,/Published 25.*Downloaded 24.*Served 20/);
  assert.doesNotMatch($('collection-model-state').textContent,/Warmup|Batch|Untrained|Lag/);
  setState({online:{...online,learner_phase:'waiting_episode'}});await w.CobotCollectionModel.refresh();w.updateButtons();
  assert.equal($('collection-start').disabled,false);
  assert.match($('collection-model-state').textContent,/120s.*waiting for a new episode.*not a failure/);
  w.eval(fs.readFileSync('segmented_frontend/preferences.js','utf8'));w.CobotPreferences.setLanguage('zh');
  assert.match($('collection-model-state').textContent,/持续学习.*120.*等待新回合.*不是故障/);
  assert.match($('collection-model-state').textContent,/逐回合.*Replay.*已发布 25.*已下载 24.*正在执行 20/);
  assert.doesNotMatch($('collection-model-state').textContent,/热身|本批|落后版数/);
  setState({online:{...online,install_error:'stage failed; retaining Actor 20'}});await w.CobotCollectionModel.refresh();w.updateButtons();
  assert.equal($('collection-start').disabled,false);
  assert.match($('collection-model-state').textContent,/stage failed; retaining Actor 20/);
  for(const fault of [{phase:'fault',ready:false,error:'learner_failed'},{phase:'install_failed',ready:false,install_error:'bad checkpoint'}]){
   setState({online:{...online,...fault}});await w.CobotCollectionModel.refresh();w.updateButtons();
   assert.equal($('collection-start').disabled,true);
  }
  setState({online,status_stale:true});await w.CobotCollectionModel.refresh();w.updateButtons();
  assert.equal($('collection-start').disabled,true);
 }finally{dom.window.close();}
});
