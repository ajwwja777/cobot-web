const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(process.env.APP_SOURCE || 'segmented_frontend/app.js','utf8');
const tick=()=>new Promise(r=>setImmediate(r));
function setup(){
 const dom=new JSDOM('<input id="rlt-data-root"><span id="rlt-recording-directory"></span><span id="episode-directory"></span><span id="next-episode"></span><button id="prepare-storage"></button>',{url:'http://localhost',runScripts:'outside-only'});
 const w=dom.window; let resolve,mode='normal',managed=null;const roots=[];
 Object.assign(w,{$:s=>w.document.querySelector(s),pendingCollectionMode:null,consoleStatus:{selected_mode:mode},rltStorageInitialized:false,historyUuid:null,historyEpisodes:[],storagePrepared:false,normalPathPicker:null,
 selectedDataRoot:()=>'/test',startPayload:()=>({data_root:'/test'}),setButtonBusy(){},saveConfig(){},updateButtons(){},message(){},showLive(){},resetReplay(){},historyMessage(){},
 request:()=>new Promise(r=>resolve=r),refreshHistory:async()=>roots.push(w.historyDataRoot())});
 w.CobotUnifiedCollection={get managedDirectory(){return managed}};
 w.eval(source.slice(source.indexOf('let historySelectedRoot'),source.indexOf('function historyEndpoint')));
 w.eval(source.slice(source.indexOf('async function prepareStorage'),source.indexOf('async function deleteHistory')));
 return{dom,w,roots,resolve:()=>resolve({data_root:'/test',episode_directory:'/test',next_episode_index:48}),rlt(){w.consoleStatus.selected_mode='rlt';managed='/explore';w.document.dispatchEvent(new w.CustomEvent('cobot:storage-used',{detail:{kind:'collection',runtime:'rlt',path:'/explore'}}));}};
}
test('late ordinary storage response cannot replace managed RLT history',async()=>{
 const x=setup();try{const pending=x.w.prepareStorage(true);x.rlt();x.resolve();await pending;await tick();assert.equal(x.w.historyDataRoot(),'/explore');assert.deepEqual(x.roots,['/explore']);assert.equal(x.w.storagePrepared,false);}finally{x.dom.window.close();}
});
test('foreign-runtime storage event is ignored and managed root wins over stale history',async()=>{
 const x=setup();try{x.rlt();x.w.document.dispatchEvent(new x.w.CustomEvent('cobot:storage-used',{detail:{kind:'collection',runtime:'normal',path:'/test'}}));assert.deepEqual(x.roots,['/explore']);assert.equal(x.w.historyDataRoot(),'/explore');await x.w.prepareStorage();assert.equal(x.w.storagePrepared,false);}finally{x.dom.window.close();}
});
test('normal storage initialization still selects its checked directory',async()=>{
 const x=setup();try{const pending=x.w.prepareStorage();x.resolve();await pending;assert.equal(x.w.storagePrepared,true);assert.equal(x.w.historyDataRoot(),'/test');}finally{x.dom.window.close();}
});
