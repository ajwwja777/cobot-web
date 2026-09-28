const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {JSDOM} = require("jsdom");
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(active = false) {
  const dom = new JSDOM(fs.readFileSync("segmented_frontend/index.html", "utf8"),
    {url:"http://localhost",runScripts:"outside-only"});
  const w=dom.window, calls=[];
  w.setInterval=()=>0;
  w.CobotPreferences={language:"en"};
  w.CobotModelChoiceLabel=m=>m.label;
  w.CobotWorkspaceUI={report(){},sessionStatus(){}};
  let state={phase:"offline",models:[],status_stale:false,data_root:"/data/evaluations/formal",
    default_data_root:"/data/evaluations/test",active:active?{id:"existing"}:null};
  w.fetch=async(path,options={})=>{
    let payload=state;
    if(path==="/api/deployment/storage") {
      const body=JSON.parse(options.body);calls.push(body);state={...state,data_root:body.data_root};payload={data_root:body.data_root};
    } else if(path.startsWith("/api/deployment/records")) payload={records:[],total:0,success:0,failure:0,aborted:0};
    else if(path==="/api/console/devices")payload={home_poses:{}};
    return {ok:true,json:async()=>payload};
  };
  for(const name of ["path_picker","deployment_ui"])
    w.eval(fs.readFileSync("segmented_frontend/"+name+".js","utf8"));
  return {dom,w,calls,getState:()=>state};
}

test("deployment refresh selects test, preserves previous path, and recent selection applies it",async()=>{
  const {dom,w,calls,getState}=setup();
  try {
    await tick();await tick();
    assert.equal(w.document.getElementById("deployment-storage").value,"/data/evaluations/test");
    assert.equal(calls.length,1);assert.equal(calls[0].reset_on_refresh,true);
    const select=w.document.getElementById("deployment-recent-directories");
    assert.ok([...select.options].some(o=>o.value==="/data/evaluations/formal"));
    select.value="/data/evaluations/formal";select.dispatchEvent(new w.Event("change"));
    await tick();
    assert.equal(getState().data_root,"/data/evaluations/formal");
    await w.CobotDeploymentUI.poll();
    assert.equal(getState().data_root,"/data/evaluations/formal");
    assert.equal(calls.length,2); // A heartbeat is not a page refresh.
    assert.equal(select.closest("label").querySelector("span").textContent,"Recent directories");
  }finally{await tick();await tick();dom.window.close();}
});

test("deployment refresh keeps an active trial directory and disables recent switching",async()=>{
  const {dom,w,calls}=setup(true);
  try {
    await tick();await tick();
    assert.equal(calls.length,0);
    assert.equal(w.document.getElementById("deployment-storage").value,"/data/evaluations/formal");
    assert.equal(w.document.getElementById("deployment-recent-directories").disabled,true);
  }finally{await tick();await tick();dom.window.close();}
});
