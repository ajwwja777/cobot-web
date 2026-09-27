"use strict";
const assert=require("assert").strict,fs=require("fs"),vm=require("vm");
const source=fs.readFileSync("segmented_frontend/app.js","utf8");
const prefix=source.slice(0,source.indexOf("for (const image of"));
const nodes=new Map(),input={value:"/data/",disabled:false,focus(){},dispatchEvent(e){this.event=e.type;}};
function element(){return {children:[],classList:{add(){},remove(){}},replaceChildren(){this.children=[]},append(x){this.children.push(x)},elements:{namedItem:()=>input}};}
function get(k){if(!nodes.has(k))nodes.set(k,element());return nodes.get(k);}
const ctx={document:{addEventListener(){},querySelector:get,querySelectorAll:()=>[],createElement:()=>({handlers:{},addEventListener(k,f){this.handlers[k]=f;}})},
  window:{SegmentedTeachUI:require("../segmented_frontend/ui.js"),CobotConsoleUI:require("../segmented_frontend/console_ui.js")},
  Event:class{constructor(type){this.type=type}},encodeURIComponent,console};
vm.createContext(ctx);vm.runInContext(prefix,ctx);
(async()=>{
  ctx.firstReply=null;
  vm.runInContext('request=()=>new Promise(resolve=>firstReply=resolve)',ctx);
  const first=vm.runInContext("suggestDirectories()",ctx);
  input.value="/data/rlt/";
  vm.runInContext('request=async()=>({directory:"/data/rlt",exists:true,directories:["/data/rlt/plug"],truncated:false})',ctx);
  await vm.runInContext("suggestDirectories()",ctx);
  ctx.firstReply({directory:"/data",exists:true,directories:["/data/old"]});await first;
  assert.equal(get("#directory-options").children.length,1);
  assert.equal(get("#directory-options").children[0].textContent,"plug/");
  get("#directory-options").children[0].handlers.click();
  assert.equal(input.value,"/data/rlt/plug/");assert.equal(input.event,"input");
  console.log("PASS late responses ignored; clicked directory completes slash and invalidates prepared config");
  vm.runInContext('request=async()=>({directory:"/data/new",exists:false,directories:[]})',ctx);
  await vm.runInContext("suggestDirectories()",ctx);
  assert.ok(get("#directory-hint").textContent.includes("创建"));
  assert.equal(get("#directory-options").children.length,0);
  console.log("PASS missing directory explains explicit create action");
})().catch(e=>{console.error(e);process.exitCode=1});
