const test=require('node:test'),assert=require('node:assert/strict'),fs=require('fs'),{JSDOM}=require('jsdom');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup(kind='vla'){
 const dom=new JSDOM(fs.readFileSync('segmented_frontend/index.html','utf8'),{runScripts:'outside-only',url:'http://localhost'}),w=dom.window,requests=[],homes=[];
 w.setInterval=()=>0;w.CobotPreferences={language:'zh'};
 w.CobotWorkspaceUI={report(){},sessionStatus(){}};w.CobotPathPicker={create:()=>({refresh(){}})};
 w.CobotDeviceUI={execute:()=>homes.push(true)};
 const model={id:'m',family:'OmniSF',task:'cover_blocks',step:30000,kind,available:true,capabilities:{start:true,pause:true,resume:true}};
 let state={models:[model],model,phase:'error',process_started:false,active:{id:'trial'},data_root:'/data',status_stale:false};
 w.fetch=async(path,opts={})=>{
  let data=state;
  if(opts.body){const body=JSON.parse(opts.body);requests.push({path,body});if(body.action==='unknown')state={...state,active:null};data=state;}
  else if(path.startsWith('/api/deployment/records'))data={records:[],total:0,success:0,failure:0,aborted:0};
  else if(path==='/api/console/devices')data={home_poses:{'front-left':['origin']}};
  return {ok:true,status:200,json:async()=>data};
 };
 for(const f of ['model_picker.js','deployment_ui.js'])w.eval(fs.readFileSync('segmented_frontend/'+f,'utf8'));
 await tick();await tick();return {dom,w,requests,homes,setState:s=>{state={...state,...s};}};
}
test('failed exited model offers unknown preservation without automatic home',async()=>{
 const {dom,w,requests,homes}=await setup();
 try{
  const b=w.document.querySelector('#deploy-unknown');assert.equal(b.disabled,false);assert.equal(b.hidden,false);
  w.document.querySelector('#deploy-auto-home').checked=true;b.click();await tick();await tick();
  assert.deepEqual(requests,[{path:'/api/deployment/action',body:{action:'unknown',model_id:'m',trial_id:'trial'}}]);assert.equal(homes.length,0);
  assert.equal(w.CobotDeploymentUI.state.active,null);
 }finally{dom.window.close();}
});
test('RLT retains its own episode skip protocol',async()=>{
 const {dom,w}=await setup('rlt');try{assert.equal(w.document.querySelector('#deploy-unknown').hidden,true);assert.equal(w.document.querySelector('#deploy-unknown').disabled,true);}finally{dom.window.close();}
});
