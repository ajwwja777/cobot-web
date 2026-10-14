const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const test=require('node:test');
const source=fs.readFileSync(require('node:path').join(__dirname,'../segmented_frontend/app.js'),'utf8');
const body=source.slice(source.indexOf('async function finishCapture('),source.indexOf('async function finalizeAndHome('));
for(const phase of ['committed','paused'])test('label error refreshes only durable history: '+phase,async()=>{
  const events=[];
  const context={captureHomeBusy:false,manualHomeBusy:false,current:{capture_state:phase},
    window:{CobotCaptureHome:{selection:()=>({enabled:true,pose:'expo'})}},
    updateButtons(){},message:(text)=>events.push(text),historyMessage(){},
    stopAndPrepareCapture:async()=>{throw Error('episode_not_found');},
    refresh:async()=>{},refreshHistory:async()=>events.push('history'),
    startCaptureHome:async()=>events.push('MOTION'),prepareStorage:async()=>events.push('prepare')};
  vm.createContext(context);vm.runInContext(body,context);await context.finishCapture(false,'failure');
  assert.equal(events.includes('history'),phase==='committed');
  assert.equal(events.includes('MOTION'),false);
  assert.equal(events.includes('prepare'),false);
  assert.equal(context.captureHomeBusy,false);
  assert.ok(events.some(x=>x.includes('episode_not_found')));
});

for(const failure of ['history','prepare'])test('successful save always unlocks next capture after '+failure+' fails',async()=>{
  const context={captureHomeBusy:false,manualHomeBusy:false,current:{capture_state:'committed'},
    window:{CobotCaptureHome:{selection:()=>({enabled:false})}},
    updateButtons(){},message(){},historyMessage(){},
    stopAndPrepareCapture:async()=>({episodeUuid:'saved',wasReviewing:true}),
    prepareStorage:async()=>{if(failure==='prepare')throw Error('storage failed');},
    revealSavedEpisode:async()=>{throw Error('history failed');}};
  vm.createContext(context);vm.runInContext(body,context);await context.finishCapture(false,'failure');
  assert.equal(context.captureHomeBusy,false);
});

test('history outage cannot invalidate prepared next episode destination',async()=>{
  const prepare=source.slice(source.indexOf('async function prepareStorage('),source.indexOf('async function deleteHistory('));
  const nodes=new Map(),$=key=>{if(!nodes.has(key))nodes.set(key,{});return nodes.get(key);};
  const context={storagePrepared:false,collectionIsRlt:()=>false,selectedDataRoot:()=>'/test',setButtonBusy(){},$,
    request:async()=>({data_root:'/test',episode_directory:'/test',next_episode_index:3}),startPayload:()=>({}),
    saveConfig(){},normalPathPicker:null,document:{dispatchEvent(){}},CustomEvent:class{},updateButtons(){},
    refreshHistory:async()=>{throw Error('history unavailable');},historyMessage(){},message(){},showLive(){}};
  vm.createContext(context);vm.runInContext(prepare,context);
  const result=await context.prepareStorage(true);
  assert.equal(result.next_episode_index,3);assert.equal(context.storagePrepared,true);
  assert.equal($('#next-episode').textContent,'episode_000003');
});
