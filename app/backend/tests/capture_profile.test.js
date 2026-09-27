"use strict";
const assert = require("assert").strict, fs = require("fs"), vm = require("vm");
const source = fs.readFileSync("segmented_frontend/app.js", "utf8");
const prefix = source.slice(0, source.indexOf("for (const image of"));
const init = source.slice(source.indexOf("async function initializeCaptureProfile"), source.indexOf("initializeCaptureProfile();"));
function setup(config, saved, fail=false) {
  const inputs = [{name:"data_root",value:"old",addEventListener(){},focus(){},disabled:false}], nodes = new Map();
  function el(){return {textContent:"",children:[],hidden:true,disabled:false,replaceChildren(){this.children=[]},append(x){this.children.push(x)},addEventListener(){},focus(){},classList:{toggle(){},add(){},remove(){}},elements:{namedItem(name){return inputs.find(x=>x.name===name)}}};}
  function get(name){if(!nodes.has(name))nodes.set(name,el());return nodes.get(name);}
  const storage = new Map(Object.entries(saved || {}));
  const ctx = {FormData:class {get(name){return inputs.find(x=>x.name===name).value;}},document:{addEventListener(){},querySelector:get,querySelectorAll:()=>inputs,createElement:()=>({value:""})},
    window:{CobotRltHome:{initialize(){}},CobotCaptureHome:{initialize(){}},SegmentedTeachUI:require("../segmented_frontend/ui.js"),CobotConsoleUI:require("../segmented_frontend/console_ui.js"),CobotPathPicker:require("../segmented_frontend/path_picker.js"),CobotDeviceUI:require("../segmented_frontend/device_control_ui.js"),CobotPostSaveHome:require("../segmented_frontend/post_save_home.js"),localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}},
    console,prepared:[],fetch:async()=>({ok:!fail,status:fail?503:200,headers:{get:()=>"application/json"},text:async()=>JSON.stringify(fail?{detail:"offline"}:config)})};
  vm.createContext(ctx);vm.runInContext(prefix+init,ctx);
  vm.runInContext('refreshCaptureHomePoses=()=>{};refreshModels=async()=>{};refresh=()=>{};refreshConsole=()=>{};prepareStorage=()=>prepared.push(document.querySelector("#capture-form").elements.namedItem("data_root").value);',ctx);
  return {ctx,inputs,get,storage};
}
(async()=>{
  const root="/media/agilex/Getea1/jiaan/data/test";
  let x=setup({profile:"left-camera-v2",normal_data_root:root,data_root_choices:["/data/rlt/plug/demonstrations"]},{"cobot-data-console-config:left-camera-v2":JSON.stringify({data_root:"/old/custom"})});
  await vm.runInContext("initializeCaptureProfile()",x.ctx);
  assert.equal(x.inputs[0].value,"/old/custom");assert.equal(x.ctx.prepared[0],"/old/custom");
  assert.deepEqual(x.get("#recording-directories").children.map(x=>x.value),["/old/custom","/data/rlt/plug/demonstrations"]);
  console.log("PASS most recently used directory overrides the server fallback");
  x.inputs[0].value="/data/rlt/plug/warmup";vm.runInContext("saveConfig()",x.ctx);
  assert.ok(JSON.parse(x.storage.get("cobot-recording-directories")).includes("/data/rlt/plug/warmup"));
  assert.equal(JSON.parse(x.storage.get("cobot-data-console-config:left-camera-v2")).data_root,"/data/rlt/plug/warmup");
  console.log("PASS selected recording path retained in choices");
  x=setup({}, {}, true);await vm.runInContext("initializeCaptureProfile()",x.ctx);
  assert.equal(x.ctx.prepared.length,0);assert.ok(x.get("#message").textContent.includes("offline"));
  console.log("PASS missing config never prepares stale recording directory");
})().catch(error=>{console.error(error);process.exitCode=1});
