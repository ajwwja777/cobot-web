const assert=require('assert').strict,vm=require('vm'),fs=require('fs');
assert.match=(s,r)=>assert(r.test(s),s+' does not match '+r);
const test=(name,fn)=>fn().then(()=>console.log('PASS '+name)).catch(e=>{console.error(name,e);process.exitCode=1});
function jsonResponse(payload){
 return {ok:true,status:200,headers:{get:()=> 'application/json'},text:async()=>JSON.stringify(payload)};
}
function setup(){
 const nodes=new Map();
 const dom=new (require('jsdom').JSDOM)('<body></body>');
 function el(tag='div'){const node=dom.window.document.createElement(tag);node.value='';node.pause=()=>{};node.load=()=>{};return node;}
 const get=k=>{if(!nodes.has(k))nodes.set(k,el());return nodes.get(k)};
 const ctx={document:{addEventListener(){},querySelector:get,querySelectorAll:()=>[],createElement:el,createTextNode:t=>t},
 window:{CobotRltHome:{manualReason:()=>'',setBusy(){},setManualBlocked(){}},CobotCaptureHome:{setBusy(){},setManualBlocked(){}},SegmentedTeachUI:require('../segmented_frontend/ui.js'),CobotConsoleUI:require('../segmented_frontend/console_ui.js')},FormData:class{get(){return '/data'}},URLSearchParams,setTimeout:()=>0,clearTimeout(){},console};
 ctx.fetch=async url=>jsonResponse(url.includes('/review')?{intervals:[],review_revision:0}:{episode_uuid:url.split('/episodes/')[1].split('?')[0],generation:4,nodes:[{node_id:7,frame_index:1,primary_trigger:'history'}]});
 vm.createContext(ctx);let src=fs.readFileSync('segmented_frontend/app.js','utf8');src=src.slice(0,src.indexOf('for (const image of'));
 vm.runInContext(src,ctx);return {ctx,get};
}
test('history remains selected across live polls and equal generations',async()=>{
 const {ctx,get}=setup();get('#episode-history').value='A';await vm.runInContext('loadHistory()',ctx);
 vm.runInContext('render({episode_uuid:"live",generation:4,nodes:[{node_id:1}],capture_state:"recording"})',ctx);
 assert.equal(get('#episode-endpoints').dataset.episodeUuid,'A');
 assert.ok([...get('#episode-endpoints').querySelectorAll('img')].some(img=>img.src.includes('/episodes/A/')));
 assert.match(get('#timeline').children[0].children[0].textContent,/7/);
 get('#episode-history').value='B';await vm.runInContext('loadHistory()',ctx);
 assert.equal(get('#episode-endpoints').dataset.episodeUuid,'B');
 assert.ok([...get('#episode-endpoints').querySelectorAll('img')].some(img=>img.src.includes('/episodes/B/')));
 assert.equal(vm.runInContext('current.episode_uuid',ctx),'live');
});
test('late selection response cannot replace newer selection',async()=>{
 const {ctx,get}=setup();const fetch=ctx.fetch;let release;
 ctx.fetch=url=>url.includes('/episodes/A?')?new Promise(resolve=>{release=()=>resolve(jsonResponse({episode_uuid:'A',generation:4,nodes:[{node_id:9}]}))}):fetch(url);
 get('#episode-history').value='A';const a=vm.runInContext('loadHistory()',ctx);
 get('#episode-history').value='B';await vm.runInContext('loadHistory()',ctx);release();await a;
 assert.equal(get('#episode-endpoints').dataset.episodeUuid,'B');
 assert.ok([...get('#episode-endpoints').querySelectorAll('img')].some(img=>img.src.includes('/episodes/B/')));
});