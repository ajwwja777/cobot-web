const assert=require("node:assert/strict");
const test=require("node:test");
const fs=require("node:fs");
const {JSDOM}=require("jsdom");
test("pointer drag selects arms, Ctrl adds/toggles, cancellation clears the box",async()=>{
 const dom=new JSDOM('<div class="infrastructure-panel"><div class="device-dashboard"></div></div>',{url:"http://localhost",runScripts:"outside-only"});
 const w=dom.window;w.setInterval=()=>0;w.fetch=async()=>({ok:false});
 w.SVGSVGElement.prototype.createSVGPoint=function(){return {x:0,y:0,matrixTransform(){return {x:this.x,y:this.y}}}};
 w.SVGSVGElement.prototype.getScreenCTM=()=>({inverse:()=>({})});
 w.SVGSVGElement.prototype.setPointerCapture=function(id){this.captured=id};
 w.SVGSVGElement.prototype.hasPointerCapture=function(id){return this.captured===id};
 w.SVGSVGElement.prototype.releasePointerCapture=function(){this.captured=null};
 w.eval(fs.readFileSync("segmented_frontend/spatial_ui.js","utf8"));w.CobotSpatialUI.mount();
 const svg=w.document.querySelector(".spatial-scene");
 function pointer(type,x,y,ctrl=false){const e=new w.MouseEvent(type,{bubbles:true,clientX:x,clientY:y,button:0,ctrlKey:ctrl});Object.defineProperties(e,{isPrimary:{value:true},pointerId:{value:1}});svg.dispatchEvent(e);}
 pointer("pointerdown",180,210);pointer("pointermove",500,280);
 assert.equal(svg.querySelector(".spatial-marquee").getAttribute("visibility"),"visible");
 pointer("pointerup",500,280);
 assert.deepEqual(Array.from(w.CobotSpatialUI.getSelection()),["front-left","mid"]);
 assert.equal(svg.querySelector(".spatial-marquee").getAttribute("visibility"),"hidden");
 pointer("pointerdown",710,350,true);pointer("pointermove",780,420,true);pointer("pointerup",780,420,true);
 assert.deepEqual(Array.from(w.CobotSpatialUI.getSelection()),["front-left","mid","rear-right"]);
 pointer("pointerdown",0,0);pointer("pointermove",940,480);pointer("pointercancel",940,480);
 assert.deepEqual(Array.from(w.CobotSpatialUI.getSelection()),["front-left","mid","rear-right"]);
 // The synthetic click immediately after a drag must not replace the box selection.
 svg.querySelector('[data-spatial="front-right"]').dispatchEvent(new w.MouseEvent("click",{bubbles:true}));
 assert.equal(w.CobotSpatialUI.getSelection().length,3);
 // Keyboard activation uses the same additive selection logic.
 svg.querySelector('[data-spatial="front-left"]').dispatchEvent(new w.KeyboardEvent("keydown",{key:"Enter",ctrlKey:true,bubbles:true}));
 assert.deepEqual(Array.from(w.CobotSpatialUI.getSelection()),["mid","rear-right"]);
 await new Promise(resolve=>setImmediate(resolve));
 dom.window.close();
});


test("pose capture uses explicit arm checkboxes and allows replacing or naming a pose",async()=>{
 const dom=new JSDOM('<div class="infrastructure-panel"><div class="device-dashboard"></div></div>',{url:"http://localhost",runScripts:"outside-only"});
 const w=dom.window;w.setInterval=()=>0;w.CobotPreferences={language:"en"};
 const devices={home_poses:{"front-left":["shared"],"rear-left":["shared"],"front-right":["shared"],"rear-right":["shared"],mid:["high"]}};
 w.fetch=async url=>({ok:url==="/api/console/devices",json:async()=>devices});
 const calls=[];
 w.CobotDeviceUI={runSelection:async(...args)=>{calls.push(args);return {job_id:"capture-1"};},
  waitForJob:async()=>({current:{phase:"completed"},payload:devices})};
 w.eval(fs.readFileSync("segmented_frontend/spatial_ui.js","utf8"));w.CobotSpatialUI.mount();
 await new Promise(resolve=>setImmediate(resolve));
 const d=w.document;
 assert.equal(d.querySelector("#spatial-capture-name").getAttribute("list"),"spatial-pose-names");
 assert.deepEqual([...d.querySelectorAll("#spatial-pose-names option")].map(x=>x.value),["high","shared"]);
 const choose=d.querySelector("#spatial-pose");choose.value="shared";choose.dispatchEvent(new w.Event("change"));
 assert.equal(d.querySelector("#spatial-capture-name").value,"shared");
 assert.equal(d.querySelector("#spatial-capture").textContent,"Replace selected arm poses");
 for(const box of d.querySelectorAll("#spatial-capture-arms input")){box.checked=box.value==="front-left";box.dispatchEvent(new w.Event("change"));}
 d.querySelector("#spatial-capture").click();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(JSON.parse(JSON.stringify(calls[0])),["capture",["front-left"],"shared"]);
 // Selecting record arms must not silently change which devices Home will move.
 assert.deepEqual([...w.CobotSpatialUI.getSelection()],["front-right"]);
 const input=d.querySelector("#spatial-capture-name");input.value="new_pose";input.dispatchEvent(new w.Event("input"));
 assert.equal(d.querySelector("#spatial-capture").textContent,"Capture new pose");
 d.querySelector("#spatial-capture").click();await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(JSON.parse(JSON.stringify(calls[1])),["capture",["front-left"],"new_pose"]);
 for(const box of d.querySelectorAll("#spatial-capture-arms input")){box.checked=false;box.dispatchEvent(new w.Event("change"));}
 assert.equal(d.querySelector("#spatial-capture").disabled,true);
 assert.equal(d.querySelector("#spatial-arm-actions").lastElementChild.id,"spatial-recover");
 dom.window.close();
});

test("individual gripper home and recovery dispatch only the selected side",async()=>{
 const dom=new JSDOM('<div class="infrastructure-panel"><div class="device-dashboard"></div></div>',{url:"http://localhost",runScripts:"outside-only"});
 const w=dom.window;w.setInterval=()=>0;w.fetch=async()=>({ok:false});w.CobotPreferences={language:"en"};
 const home=[],recover=[];
 w.CobotDeviceUI={execute:async spec=>home.push(spec),runRecover:async target=>{recover.push(target);return null;}};
 w.eval(fs.readFileSync("segmented_frontend/spatial_ui.js","utf8"));w.CobotSpatialUI.mount();
 await new Promise(resolve=>setImmediate(resolve));
 for(const target of ["gripper-left","gripper-right"]){
  w.document.querySelector('[data-spatial="'+target+'"]').dispatchEvent(new w.MouseEvent("click",{bubbles:true}));
  assert.equal(w.document.querySelector("#spatial-pose-recording").hidden,true);
  assert.equal(w.document.querySelector("#spatial-home").disabled,false);
  w.document.querySelector("#spatial-home").click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(home.at(-1).target,target);
  w.document.querySelector("#spatial-recover").click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(recover.at(-1),target);
 }
 assert.deepEqual(home.map(x=>x.target),["gripper-left","gripper-right"]);
 assert.deepEqual(recover,["gripper-left","gripper-right"]);
 dom.window.close();
});
