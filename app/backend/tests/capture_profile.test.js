"use strict";
const assert = require("assert").strict, fs = require("fs"), vm = require("vm");
const source = fs.readFileSync("segmented_frontend/app.js", "utf8");
const prefix = source.slice(0, source.indexOf("for (const image of"));
const init = source.slice(source.indexOf("async function initializeCaptureProfile"), source.indexOf("initializeCaptureProfile();"));
function setup(config, saved, fail=false, capture={capture_state:"idle"}) {
  const inputs = [{name:"data_root",value:"old",addEventListener(){},focus(){},disabled:false}], nodes = new Map();
  function el(){return {textContent:"",children:[],hidden:true,disabled:false,replaceChildren(){this.children=[]},append(x){this.children.push(x)},addEventListener(){},focus(){},classList:{toggle(){},add(){},remove(){}},elements:{namedItem(name){return inputs.find(x=>x.name===name)}}};}
  function get(name){if(!nodes.has(name))nodes.set(name,el());return nodes.get(name);}
  const storage = new Map(Object.entries(saved || {}));
  const ctx = {FormData:class {get(name){return inputs.find(x=>x.name===name).value;}},document:{addEventListener(){},querySelector:get,querySelectorAll:()=>inputs,createElement:()=>({value:""})},
    window:{CobotRltHome:{initialize(){}},CobotCaptureHome:{initialize(){}},SegmentedTeachUI:require("../segmented_frontend/ui.js"),CobotConsoleUI:require("../segmented_frontend/console_ui.js"),CobotPathPicker:require("../segmented_frontend/path_picker.js"),CobotDeviceUI:require("../segmented_frontend/device_control_ui.js"),CobotPostSaveHome:require("../segmented_frontend/post_save_home.js"),localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}},
    console,prepared:[],fetch:async(path)=>({ok:!fail,status:fail?503:200,headers:{get:()=>"application/json"},text:async()=>JSON.stringify(fail?{detail:"offline"}:path==="/api/segmented-teach/status"?capture:config)})};
  vm.createContext(ctx);vm.runInContext(prefix+init,ctx);
  vm.runInContext('refreshCaptureHomePoses=()=>{};refreshModels=async()=>{};refresh=()=>{};refreshConsole=()=>{};prepareStorage=()=>prepared.push(document.querySelector("#capture-form").elements.namedItem("data_root").value);',ctx);
  return {ctx,inputs,get,storage};
}
(async()=>{
  const root="/media/agilex/Getea1/jiaan/data/datasets/test";
  let x=setup({profile:"left-camera-v2",normal_data_root:"/formal",test_data_root:root,data_root_choices:["/data/rlt/plug/demonstrations"]},{"cobot-data-console-config:left-camera-v2":JSON.stringify({data_root:"/old/custom"})});
  await vm.runInContext("initializeCaptureProfile()",x.ctx);
  assert.equal(x.inputs[0].value,root);assert.equal(x.ctx.prepared[0],root);
  assert.deepEqual(x.get("#recording-directories").children.map(x=>x.value),[root,"/data/rlt/plug/demonstrations","/old/custom"]);
  console.log("PASS refresh defaults to test and preserves the previous directory as a choice");
  x.inputs[0].value="/data/rlt/plug/warmup";vm.runInContext("saveConfig()",x.ctx);
  assert.ok(JSON.parse(x.storage.get("cobot-recording-directories")).includes("/data/rlt/plug/warmup"));
  assert.equal(JSON.parse(x.storage.get("cobot-data-console-config:left-camera-v2")).data_root,"/data/rlt/plug/warmup");
  console.log("PASS selected recording path retained in choices");
  x=setup({profile:"active",normal_data_root:"/formal",test_data_root:root},{},false,{capture_state:"paused",data_root:"/active/recording"});
  await vm.runInContext("initializeCaptureProfile()",x.ctx);
  assert.equal(x.inputs[0].value,"/active/recording");assert.equal(x.ctx.prepared.length,0);
  console.log("PASS refreshing a paused recording preserves its actual destination");
  x=setup({profile:"rlt",normal_data_root:root,test_data_root:root},{});
  await vm.runInContext("initializeCaptureProfile()",x.ctx);
  x.ctx.storageReply={data_root:"/previous/rlt",can_reset_on_refresh:true,recent_data_roots:["/previous/rlt"],editable:true};
  x.ctx.resetCalls=[];
  vm.runInContext('request=async(path,options)=>{if(options){resetCalls.push(JSON.parse(options.body));storageReply={...storageReply,data_root:JSON.parse(options.body).data_root};}return storageReply;};refreshRltHistory=async()=>{};selectedModel=()=>null;',x.ctx);
  await vm.runInContext("refreshRltStorage()",x.ctx);
  assert.equal(x.ctx.resetCalls.length,1);assert.equal(x.ctx.resetCalls[0].data_root,root);
  assert.equal(x.ctx.resetCalls[0].reset_on_refresh,true);
  x.ctx.storageReply.data_root="/manually/selected";
  await vm.runInContext("refreshRltStorage()",x.ctx);
  assert.equal(x.ctx.resetCalls.length,1);assert.equal(x.get("#rlt-data-root").value,"/manually/selected");
  assert.ok(x.ctx.window.CobotRecordingDirectories.includes("/previous/rlt"));
  console.log("PASS RLT resets once on refresh, retains history, and respects later manual choices");
  vm.runInContext("rltStorageInitialized=false",x.ctx);
  x.ctx.storageReply.can_reset_on_refresh=false;
  await vm.runInContext("refreshRltStorage()",x.ctx);
  assert.equal(x.ctx.resetCalls.length,1);
  console.log("PASS active RLT recording does not reset on refresh");
  x=setup({}, {}, true);await vm.runInContext("initializeCaptureProfile()",x.ctx);
  assert.equal(x.ctx.prepared.length,0);assert.ok(x.get("#message").textContent.includes("offline"));
  console.log("PASS missing config never prepares stale recording directory");
})().catch(error=>{console.error(error);process.exitCode=1});
