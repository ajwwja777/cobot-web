const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {JSDOM} = require("jsdom");

test("site paths and defaults save without launching; browse and translation preserve controls", async () => {
  const dom = new JSDOM('<div id="settings-drawer"><div class="settings-head"></div></div><div id="capture-model-actions"></div><dl id="deployment-facts"></dl><select id="capture-model-select"><option value="one">one</option></select>',
    {url:"http://localhost",runScripts:"outside-only"});
  const w=dom.window, calls=[];
  w.CobotPreferences={language:"zh"};
  w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute("open","");};
  w.HTMLDialogElement.prototype.close=function(){this.removeAttribute("open");};
  const options={hardware:{},defaults:{arms:"/site/arms.sh",cameras:"/site/cameras.sh",setup:"/site/setup.bash"},
    templates:[{id:"pi05-in-the-pot",label:"π0.5"}],models:[{id:"one",checkpoint:"/model/one",label:"one",available:true}],selected_model:"one"};
  w.fetch=async(path,init={})=>{
    calls.push([path,init.body&&JSON.parse(init.body)]);
    let result=options;
    if(path.startsWith("/api/site/browse"))result={path:"/model",parent:"",entries:[{path:"/model/chosen",name:"chosen",directory:false}]};
    return {ok:true,json:async()=>result};
  };
  const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
  try{
    w.eval(fs.readFileSync("segmented_frontend/site_options.js","utf8"));
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    await w.CobotSiteOptions.open();
    assert(w.document.querySelector("#site-options").open);
    const buttons=()=>[...w.document.querySelectorAll("#site-options button")];
    buttons().find(b=>b.textContent==="保存默认选择").click();await settle();
    assert(calls.some(([path,body])=>path==="/api/site/default"&&body.model_id==="one"));
    buttons().find(b=>b.textContent==="机械臂 / 相机").click();
    w.document.querySelector("#site-launch").value="/site/custom.sh";
    buttons().find(b=>b.textContent==="保存启动配置").click();await settle();
    assert(calls.some(([path,body])=>path==="/api/site/hardware"&&body.path==="/site/custom.sh"));
    w.document.querySelector("#site-checkpoint").parentElement.querySelector("button").click();await settle();
    buttons().find(b=>b.textContent==="chosen").click();
    assert.equal(w.document.querySelector("#site-checkpoint").value,"/model/chosen");
    // Match the real preference translator's textContent replacement.
    for(const node of w.document.querySelectorAll("[data-zh][data-en]"))node.textContent=node.dataset.en;
    assert(w.document.querySelector("#site-compatible"));
    assert(w.document.querySelector("#site-make-default"));
    assert(!calls.some(([path])=>/devices\/action|deployment\/action|collection\/model/.test(path)));
  }finally{dom.window.close();}
});
