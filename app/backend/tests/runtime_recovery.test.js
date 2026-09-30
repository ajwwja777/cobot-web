const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),{JSDOM}=require("jsdom");
function setup(){
 const dom=new JSDOM('<section id="help"></section>',{runScripts:"outside-only",url:"http://localhost"}),w=dom.window,calls=[];
 w.CobotPreferences={language:"en"};w.fetch=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>({phase:"loading",model_retained:true,manual_start_required:true})};};
 w.eval(fs.readFileSync("segmented_frontend/recorder_recovery.js","utf8"));
 const node=w.document.querySelector("#help"),ui=w.CobotRuntimeRecovery.create(node);
 return {dom,w,calls,node,ui};
}
const failed={phase:"error",runtime_failure:{code:"rtc_delay_exceeded",cause:"RTC delay exceeded budget",stage1_retained:true,stage1_pid:123,recoverable:true}};
test("runtime fault uses explicit recovery and never load/start/unload",async()=>{
 const {dom,calls,node,ui}=setup();ui.update(failed);
 assert.match(node.textContent,/RTC execution timed out/);assert.match(node.textContent,/Stage1 weights remain loaded/);
 node.querySelector(".runtime-recover").click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls.length,1);assert.equal(calls[0][0],"/api/rlt/recover-runtime");assert.equal(calls[0][1].method,"POST");
 assert.match(node.textContent,/start the session manually/);
 ui.update({phase:"ready"});assert.equal(node.hidden,true);dom.window.close();
});
test("uncertain state, pending evaluation and missing Stage1 disable recovery",()=>{
 const {dom,node,ui}=setup();
 for(const extra of [{status_stale:true},{operation:"load"},{active:{id:"pending"}},{runtime_failure:{...failed.runtime_failure,recoverable:false,stage1_retained:false}}]){
   ui.update({...failed,...extra});assert.equal(node.querySelector(".runtime-recover").disabled,true);
 }
 dom.window.close();
});
test("checking status never invokes recovery and cause uses text only",async()=>{
 const {dom,calls,node,ui}=setup();ui.update({...failed,runtime_failure:{...failed.runtime_failure,cause:'<img src=x onerror=alert(1)>'}});
 assert.equal(node.querySelector("img"),null);
 node.querySelector(".runtime-check").click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(calls[0][0],"/api/deployment/status");assert.equal(calls[0][1].method,undefined);dom.window.close();
});
test("locale changes title and recovery labels",()=>{
 const {dom,w,node,ui}=setup();w.CobotPreferences.language="zh";ui.update(failed);
 assert.match(node.textContent,/收尾录制/);assert.doesNotMatch(node.textContent,/RTC execution timed out/);dom.window.close();
});


test("general restart remains available for a stuck live Session",async()=>{
 const {dom,calls,node,ui}=setup();
 ui.update({phase:"paused",model:{kind:"rlt"},runtime_recovery_available:true});
 assert.equal(node.hidden,false);
 assert.equal(node.querySelector(".runtime-recover").hidden,true);
 assert.equal(node.querySelector(".runtime-restart").disabled,false);
 node.querySelector(".runtime-restart").click();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(JSON.parse(calls[0][1].body),{finalize_pending:true,restart_running:true});
 assert.match(node.textContent,/start the session manually/);
 dom.window.close();
});
test("targeted recovery explicitly finalizes pending recording without labeling",async()=>{
 const {dom,calls,node,ui}=setup();ui.update(failed);
 node.querySelector(".runtime-recover").click();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(JSON.parse(calls[0][1].body),{finalize_pending:true});
 assert.match(node.textContent,/without a success\/failure label/);
 dom.window.close();
});
