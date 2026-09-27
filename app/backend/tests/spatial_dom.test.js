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
