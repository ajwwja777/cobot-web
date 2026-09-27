const assert=require("node:assert/strict");
const test=require("node:test");
const {selectionInBox}=require("../segmented_frontend/spatial_ui.js");
const points={"front-left":{x:205,y:250},mid:{x:470,y:250},"front-right":{x:735,y:250},"rear-left":{x:205,y:390},"rear-right":{x:735,y:390}};
test("box selects arm bases and replaces selection",()=>{
 assert.deepEqual(selectionInBox(points,{left:180,right:500,top:200,bottom:270},["rear-right"]),["front-left","mid"]);
});
test("Ctrl box adds without duplicates; empty box preserves previous",()=>{
 assert.deepEqual(selectionInBox(points,{left:180,right:500,top:200,bottom:270},["mid","rear-right"],true),["mid","rear-right","front-left"]);
 assert.deepEqual(selectionInBox(points,{left:0,right:10,top:0,bottom:10},["rear-right"],true),["rear-right"]);
 assert.deepEqual(selectionInBox(points,{left:0,right:10,top:0,bottom:10},["rear-right"]),[]);
});
