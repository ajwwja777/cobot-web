const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('segmented_frontend/app.js','utf8');
test('old node-only result is readonly and never requests nonexistent HDF5 labels',async()=>{
 const dom=new JSDOM('<div id="labels"></div>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 try{
  let calls=0;w.fetch=async()=>{calls++;throw Error('unexpected request');};
  w.eval(fs.readFileSync('segmented_frontend/history_labels.js','utf8'));
  const widget=w.CobotHistoryLabels.create(w.document.querySelector('#labels'));
  await widget.update({episode_uuid:'old',labelable:false,episode_outcome:'failure'},'/test');
  const select=w.document.querySelector('select');assert.equal(select.value,'failure');assert.equal(select.disabled,true);
  assert.equal(calls,0);assert.doesNotMatch(w.document.body.textContent,/episode_not_found|labels_invalid/);
 }finally{dom.window.close();}
});
test('unavailable video hides player without polling or throwing a capture error',async()=>{
 const dom=new JSDOM('<div id="node-preview"></div><div id="replay-panel"></div><span id="replay-status"></span><video id="episode-video"></video>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 try{
  let polls=0,errors=0;
  Object.assign(w,{$:s=>w.document.querySelector(s),historyUuid:'old',viewedEpisode:{episode_uuid:'old'},selectionToken:1,
   preloadedVideos:new Map(),historyIsRlt:()=>false,historyEndpoint:p=>p,
   request:async()=>({state:'unavailable',reason:'Only endpoint images'}),historyMessage:()=>errors++,setTimeout:()=>polls++});
  w.eval(source.slice(source.indexOf('async function loadReplay('),source.indexOf('function renderReview(')));
  await w.loadReplay();assert.equal(w.document.querySelector('video').hidden,true);
  assert.equal(w.document.querySelector('#replay-status').textContent,'Only endpoint images');
  assert.equal(polls,0);assert.equal(errors,0);
 }finally{dom.window.close();}
});


test('history waits for profile initialization instead of querying the default directory',async()=>{
 const dom=new JSDOM('',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 try{
  w.CobotCaptureProfileReady=false;let calls=0;w.request=async()=>{calls++;throw Error('must not fetch');};
  w.eval(source.slice(source.indexOf('async function refreshHistory('),source.indexOf('async function preloadEpisodeMedia(')));
  assert.equal(await w.refreshHistory({loadSelected:true}),0);assert.equal(calls,0);
 }finally{dom.window.close();}
});
for(const stale of [true,false])test('episode detail error is discarded only after its directory changes: '+stale,async()=>{
 const dom=new JSDOM('<select id="episode-history"><option value="old">old</option></select><div id="timeline"></div><div id="node-preview"></div><div id="review-panel"></div>',{url:'http://localhost',runScripts:'outside-only'}),w=dom.window;
 try{
  let reject,root='/old';
  Object.assign(w,{$:s=>w.document.querySelector(s),historyEpisodes:[{episode_uuid:'old',history_format:'segmented'}],selectionToken:0,
   updateHistoryLabels(){},historyUuid:null,currentReview:null,resetReplay(){},preloadedEpisodeDetails:new Map(),
   episodeDetailKey:x=>x,historyIsRlt:()=>false,showEpisodeEndpoints(){},historyDataRoot:()=>root,historyEndpoint:p=>p,
   request:()=>new Promise((_,fail)=>reject=fail)});
  w.eval(source.slice(source.indexOf('async function loadHistory('),source.indexOf('async function loadReplay(')));
  const pending=w.loadHistory();if(stale){root='/expo';w.selectionToken++;}
  reject(Error('episode_not_found'));
  if(stale)await pending;else await assert.rejects(pending,/episode_not_found/);
 }finally{dom.window.close();}
});
