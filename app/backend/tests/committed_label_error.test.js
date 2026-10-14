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
