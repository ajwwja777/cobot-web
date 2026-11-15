const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function setup(){
 const base=path.join(__dirname,'../segmented_frontend');
 const dom=new JSDOM(fs.readFileSync(path.join(base,'index.html'),'utf8'),{url:'http://localhost:8015/',runScripts:'outside-only'}),w=dom.window,calls=[];
 w.setInterval=()=>0;w.HTMLMediaElement.prototype.pause=function(){};
 let rows=[{id:'motion-test',saved_at:1700000000,state:'saved',total_bytes:3*1024**3,files:['camera_left','camera_high','camera_right'].map(camera=>({camera,bytes:1024**3,playable:true,download_url:'/api/videos/file?camera='+camera}))}];
 w.fetch=async(url,options={})=>{calls.push({url,method:options.method||'GET'});return {ok:true,json:async()=>url.includes('/api/videos/records')?{records:rows}:url.includes('/api/motion/records')?{records:[]}:{active:false,default_data_root:'/data'}};};
 for(const file of ['motion_ui.js','video_downloads.js'])w.eval(fs.readFileSync(path.join(base,file),'utf8'));
 await tick();return {dom,w,calls,setRows:value=>rows=value};
}
test('video tab shows three native MP4 players and direct download links without mutations',async()=>{
 const {dom,w,calls}=await setup();try{
 w.document.querySelector('#motion-deployment-videos').click();await tick();
 assert.equal(w.document.querySelectorAll('#video-download-files video').length,3);
 for(const a of w.document.querySelectorAll('#video-download-files a'))assert.ok(a.hasAttribute('download')&&a.href.startsWith('http://localhost:8015/api/videos/file?'));
 assert.equal(w.document.querySelectorAll('#video-download-files a').length,3);
 assert.match(w.document.querySelector('#video-download-info').textContent,/3.00 GiB/);
 assert.ok([...w.document.querySelectorAll('video')].some(v=>v.src.includes('download=false')));
 assert.ok(calls.every(c=>c.method==='GET'));
 }finally{dom.window.close();}
});
test('directory change clears stale links; invalid external URLs and hidden shortcuts blocked',async()=>{
 const {dom,w,calls,setRows}=await setup();try{
 w.document.querySelector('[data-page="deployment"]').classList.add('active');
 w.document.querySelectorAll('[data-page]:not([data-page="deployment"])').forEach(n=>n.classList.remove('active'));
 w.document.querySelector('#motion-deployment-videos').click();await tick();
 let shortcuts=0;w.document.addEventListener('keydown',()=>shortcuts++);
 w.document.body.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));assert.equal(shortcuts,0);
 setRows([{id:'bad',saved_at:1,state:'saved',files:[{camera:'camera_left',playable:true,download_url:'https://evil.example/api/videos/file'}]}]);
 const root=w.document.querySelector('#video-download-root');root.value='/data/new folder';root.dispatchEvent(new w.Event('change'));
 assert.equal(w.document.querySelectorAll('#video-download-files a').length,0);await tick();
 assert.equal(w.document.querySelectorAll('#video-download-files a').length,0);
 assert.ok(calls.some(c=>c.url.includes('%2Fdata%2Fnew%20folder')));assert.ok(calls.every(c=>c.method==='GET'));
 }finally{dom.window.close();}
});
