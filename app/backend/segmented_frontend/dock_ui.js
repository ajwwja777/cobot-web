"use strict";
(() => {
  const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
  const key="cobot-workspace-docks-v1", edges=["top","bottom","left","right"];
  const defaults={modules:{camera:"top",outputs:"bottom"},open:{top:true,bottom:false,left:true,right:false},active:{top:"camera",bottom:"outputs"},sizes:{top:280,bottom:310,left:310,right:350},sidebar:216,nav:["system","operation","training","deployment"]};
  let layout, shell, main, modules={}, panels={}, toggles={}, maximized=null, selectedTask="", snapshot=null, fetching=false, stopping=false, commandMode="current", commandIndex=0;
  let outputWasVisible=false, pendingTask=null, settingsHostPanel=null, settingsCloseTimer=null, commandGroup='',commandChoice='current';
  let initialized=false, lastTasks="", lastOutput="", selectedComponent="", follow=true;
  const phase={running:"运行中",ready:"就绪",paused:"暂停",loading:"加载中",completed:"完成",failed:"失败",stopping:"停止中",stopped:"已停止",stale:"已退出",error:"异常",offline:"未运行",archived:"历史",previous:"最近输出",model:"模型日志",invalid:"无任务"};
  const names={can:"CAN",roscore:"ROS",arms:"机械臂",cameras:"相机",home:"归位",pose:"位姿",recover:"恢复",rlt:"RLT",deployment:"部署",stage1:"Stage 1",pi05:"π0.5",rlt_model:"模型"};
  const paths={left:'M9 3v18',right:'M15 3v18',top:'M3 9h18',bottom:'M3 15h18',camera:'M8 5l2-2h4l2 2h4v15H4V5h4M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',outputs:'M6 8l4 4-4 4m7 0h5',operation:'M4 6h16v15H4zM8 3h8v6H8zM8 13h8m-8 4h5',training:'M3 20h18M5 16l4-6 4 3 6-9M17 4h2v2',deployment:'M7 4l13 8-13 8z',expand:'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6',close:'M6 6l12 12M18 6 6 18',copy:'M8 8h12v13H8zM16 8V3H3v13h5',refresh:'M20 8a8 8 0 1 0 0 8M20 3v5h-5'};
  function icon(name){return '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'+(edges.includes(name)?'<rect x="3" y="3" width="18" height="18" rx="2"/>':name==="outputs"?'<rect x="2" y="3" width="20" height="18" rx="2"/>':'')+'<path d="'+(paths[name]||paths.outputs)+'"/></svg>';}
  function node(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;}
  function button(label,fn,ico){const b=node("button","dock-button",ico?null:label);b.type="button";b.title=label;b.setAttribute("aria-label",label);if(ico){b.innerHTML=icon(ico);if(["close","expand","copy","refresh"].includes(ico))b.classList.add("panel-tool-button");}b.addEventListener("click",fn);return b;}
  function save(){try{localStorage.setItem(key,JSON.stringify(layout));}catch(_){}}
  function restore(){try{const s=JSON.parse(localStorage.getItem(key)||"{}");const result={...defaults,...s,navOpen:s.navOpen??s.open?.left??true,commands:{visible:true,width:350,height:220,...s.commands},modules:{...defaults.modules,...s.modules},open:{...defaults.open,...s.open},active:{...defaults.active,...s.active},sizes:{...defaults.sizes,...s.sizes}};if(s.navOpen==null&&Object.values(result.modules).includes('left'))result.open.left=true;return result;}catch(_){return {...JSON.parse(JSON.stringify(defaults)),navOpen:true,commands:{visible:true,width:350,height:220}};}}
  function report(text,error=false){window.CobotWorkspaceUI?.report(text,error?"error":"success","输出");$("#output-feedback").textContent=text;}
  async function json(url,body){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetch(url,{cache:"no-store",signal:controller.signal,...(body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{})});const value=await r.json();if(!r.ok)throw Error(value.detail||value.error||r.status);return value;}catch(e){if(controller.signal.aborted)throw Error("请求超时，正在核对任务状态");throw e;}finally{clearTimeout(timer);}}
  async function copy(text){if(!text)return;try{if(navigator.clipboard&&isSecureContext)await navigator.clipboard.writeText(text);else{const area=node("textarea");area.value=text;area.style.cssText="position:fixed;opacity:0;top:0;left:0";document.body.append(area);area.select();if(!document.execCommand("copy"))throw Error();area.remove();}report("已复制");}catch(_){report("复制失败，请直接选中命令复制",true);}}
  function moduleVisible(name){const edge=layout.modules[name];return layout.open[edge]&&layout.active[edge]===name&&(!maximized||maximized===edge);}
  function fitDocks(){
    // Retain the preferred sizes, but always leave the center and toolbar visible.
    for(const [pair,budget] of [[['top','bottom'],Math.max(0,shell.clientHeight-207)],[['left','right'],Math.max(0,shell.clientWidth-298)]]){
      const visible=pair.filter(edge=>shell.classList.contains('has-'+edge));
      const wanted=visible.map(edge=>Math.max(140,Number(layout.sizes[edge])||280));
      const total=wanted.reduce((a,b)=>a+b,0),scale=total>budget?budget/total:1;
      visible.forEach((edge,index)=>shell.style.setProperty('--dock-'+edge,Math.floor(wanted[index]*scale)+'px'));
    }
  }
  function apply(){
    for(const edge of edges){
      const items=Object.keys(modules).filter(name=>layout.modules[name]===edge);
      if(!items.length&&edge!=="left")layout.open[edge]=false;
      if(!items.includes(layout.active[edge]))layout.active[edge]=items[0]||"";
      const visible=Boolean(layout.open[edge]&&items.length);
      const panel=panels[edge];panel.hidden=!visible||(maximized&&maximized!==edge);panel.classList.toggle("maximized",maximized===edge);
      panel.querySelector(".dock-empty").hidden=Boolean(items.length);
      for(const name of Object.keys(modules)){
        if(layout.modules[name]!==edge)continue;
        const tab=panel.tabBar.querySelector('[data-module="'+name+'"]');
        if(!tab){const b=button(name==="camera"?"相机":"输出",()=>{layout.active[edge]=name;layout.open[edge]=true;save();apply();});b.className="dock-tab";b.dataset.module=name;b.innerHTML=icon(name)+'<span>'+(name==="camera"?"相机":"输出")+'</span>';bindModuleDrag(b,name);panel.tabBar.append(b);}
        const module=modules[name];if(module.parentElement!==panel.querySelector(".dock-content"))panel.querySelector(".dock-content").append(module);
        module.hidden=!visible||layout.active[edge]!==name||(maximized&&maximized!==edge);
      }
      for(const tab of panel.tabBar.querySelectorAll(".dock-tab")){if(!items.includes(tab.dataset.module))tab.remove();else{tab.classList.toggle("selected",tab.dataset.module===layout.active[edge]);tab.setAttribute("aria-selected",String(tab.dataset.module===layout.active[edge]));}}
      toggles[edge].classList.toggle("selected",edge==='left'?layout.navOpen:layout.open[edge]);toggles[edge].setAttribute("aria-expanded",String(edge==='left'?layout.navOpen:layout.open[edge]));
      shell.style.setProperty('--dock-'+edge,visible?Math.max(140,Number(layout.sizes[edge])||280)+'px':'0px');
      shell.classList.toggle('has-'+edge,visible);
    }
    shell.classList.toggle("dock-maximized",Boolean(maximized));
    const top=panels.top,topVisible=!top.hidden;
    top.querySelector('.dock-head').hidden=topVisible;
    (topVisible?$('.workspace-tools'):top.querySelector('.dock-head')).append(top.tabBar);
    (topVisible?$('.workspace-top-actions'):top.querySelector('.dock-head')).append(top.actions);
    for(const b of $$(".workspace-module")){const edge=layout.modules[b.dataset.module];b.hidden=!panels[edge].hidden;b.classList.toggle("selected",moduleVisible(b.dataset.module));b.setAttribute("aria-pressed",String(moduleVisible(b.dataset.module)));}
    const collapsed=!layout.navOpen||innerWidth<=900;
    $(".sidebar").classList.toggle("is-collapsed",collapsed);document.body.classList.toggle("sidebar-is-collapsed",collapsed);
    document.documentElement.style.setProperty("--workbench-sidebar",collapsed?"64px":Math.max(170,Math.min(320,layout.sidebar))+"px");
    fitDocks();
    applyCommands();positionSettings();
    main.dataset.cameraDock="closed";
    window.CobotWorkspaceUI?.cameraChanged?.();
    const outputNow=moduleVisible("outputs");if(outputNow&&!outputWasVisible)refreshOutputs();outputWasVisible=outputNow;
    if(outputNow&&follow)requestAnimationFrame(()=>{const log=$('#task-log');log.scrollTop=log.scrollHeight;});
  }
  function place(name,edge){if(!modules[name]||!edges.includes(edge))return;layout.modules[name]=edge;layout.active[edge]=name;layout.open[edge]=true;maximized=null;save();apply();}
  function openModule(name){const edge=layout.modules[name];maximized=null;layout.open[edge]=true;layout.active[edge]=name;save();apply();}
  function bindModuleDrag(target,name){target.draggable=true;target.addEventListener("dragstart",event=>{event.dataTransfer.setData("application/x-cobot-module",name);event.dataTransfer.setData("text/plain",name);event.dataTransfer.effectAllowed="move";shell.classList.add("is-docking");});target.addEventListener("dragend",()=>{shell.classList.remove("is-docking");$$(".dock-drop-zone").forEach(n=>n.classList.remove("hover"));});}
  function resize(handle,key,axis,reverse=false){
    handle.tabIndex=0;handle.setAttribute("role","separator");handle.setAttribute("aria-orientation",axis==="x"?"vertical":"horizontal");handle.setAttribute("aria-label",key==="sidebar"?"调整导航宽度":"调整面板大小");
    const value=()=>key==="sidebar"?layout.sidebar:(parseFloat(shell.style.getPropertyValue('--dock-'+key))||layout.sizes[key]);
    function set(size){const max=key==="sidebar"?320:Math.max(180,(axis==="x"?shell.clientWidth:shell.clientHeight)*.65);size=Math.max(key==="sidebar"?170:140,Math.min(max,size));if(key==="sidebar")layout.sidebar=size;else layout.sizes[key]=size;handle.setAttribute("aria-valuenow",Math.round(size));apply();}
    handle.addEventListener("pointerdown",event=>{if(event.button!==0)return;event.preventDefault();handle.setPointerCapture(event.pointerId);const at=axis==="x"?event.clientX:event.clientY,start=value();document.body.classList.add("dock-resizing");const move=e=>set(start+((axis==="x"?e.clientX:e.clientY)-at)*(reverse?-1:1));const end=()=>{document.removeEventListener("pointermove",move,true);document.removeEventListener("pointerup",end,true);document.removeEventListener("pointercancel",end,true);document.body.classList.remove("dock-resizing");save();};document.addEventListener("pointermove",move,true);document.addEventListener("pointerup",end,true);document.addEventListener("pointercancel",end,true);});
    handle.addEventListener("keydown",event=>{if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;event.preventDefault();set(value()+(["ArrowRight","ArrowDown"].includes(event.key)?20:-20)*(reverse?-1:1));save();});
    handle.addEventListener("dblclick",()=>{set(key==="sidebar"?defaults.sidebar:defaults.sizes[key]);save();});
  }
  function makePanel(edge){const p=node("section","tool-dock");p.dataset.edge=edge;p.setAttribute("aria-label",({top:"上",bottom:"下",left:"左",right:"右"})[edge]+"侧栏");const head=node("header","dock-head"),tabs=node("div","dock-tabs"),actions=node("div","dock-actions");tabs.setAttribute("role","tablist");
    actions.append(button("展开 / 还原",()=>{maximized=maximized===edge?null:edge;apply();},"expand"),button("收起面板",()=>{layout.open[edge]=false;maximized=null;save();apply();},"close"));head.append(tabs,actions);p.tabBar=tabs;p.actions=actions;const content=node("div","dock-content"),empty=node("div","dock-empty","拖入相机或输出");content.append(empty);const grip=node("div","dock-resizer");resize(grip,edge,["left","right"].includes(edge)?"x":"y",["bottom","right"].includes(edge));p.append(head,content,grip);shell.append(p);return p;}
  function buildShell(){main=$("main");shell=node("div","workbench-shell");main.before(shell);const bar=node("header","workspace-bar"),tools=node("div","workspace-tools"),controls=node("div","workspace-layout");
    for(const name of ["camera","outputs"]){const b=button(name==="camera"?"相机":"输出",()=>{const edge=layout.modules[name];if(moduleVisible(name)){layout.open[edge]=false;save();apply();}else openModule(name);});b.className="workspace-module";b.dataset.module=name;b.innerHTML=icon(name)+'<span>'+(name==="camera"?"相机":"输出")+'</span>';bindModuleDrag(b,name);tools.append(b);}
    for(const edge of ["left","bottom","right","top"]){const b=button(({left:"左侧导航",bottom:"下侧栏",right:"右侧栏",top:"上侧栏"})[edge],()=>{if(edge==='left'){layout.navOpen=!layout.navOpen;save();apply();return;}layout.open[edge]=!layout.open[edge];if(layout.open[edge]&&!Object.values(layout.modules).includes(edge)){place(edge==="top"?"camera":"outputs",edge);}else{maximized=null;save();apply();}},edge);toggles[edge]=b;controls.append(b);}
    bar.append(tools,node('div','workspace-top-actions'),controls);shell.append(bar,main);main.classList.add("workbench-center");
    for(const edge of edges){panels[edge]=makePanel(edge);const zone=node("div","dock-drop-zone",({top:"停靠到上侧",bottom:"停靠到下侧",left:"停靠到左侧",right:"停靠到右侧"})[edge]);zone.dataset.edge=edge;zone.addEventListener("dragover",event=>{if(!event.dataTransfer.types.includes("application/x-cobot-module"))return;event.preventDefault();zone.classList.add("hover");});zone.addEventListener("dragleave",()=>zone.classList.remove("hover"));zone.addEventListener("drop",event=>{event.preventDefault();place(event.dataTransfer.getData("application/x-cobot-module"),edge);shell.classList.remove("is-docking");zone.classList.remove("hover");});shell.append(zone);}
    const camera=$("#camera-dock");camera.classList.remove("is-pinned");camera.style.cssText="";modules.camera=camera;$("#camera-pin").hidden=true;
    window.CobotWorkspaceUI.cameraVisible=()=>moduleVisible("camera");
    const observer=new ResizeObserver(entries=>{for(const entry of entries)entry.target.classList.toggle("narrow",entry.contentRect.width<620);applyCommands();});Object.values(panels).forEach(p=>observer.observe(p));
    $("#camera-nav").classList.add("dock-nav-hidden");$(".nav-item[data-view=outputs]").classList.add("dock-nav-hidden");$(".nav-item[data-view=host]").classList.add("dock-nav-hidden");
    $(".nav-item[data-view=outputs]").addEventListener("click",event=>{event.stopImmediatePropagation();openModule("outputs");},true);
    const oldToggle=$(".sidebar-toggle");oldToggle.addEventListener("click",event=>{event.stopImmediatePropagation();layout.navOpen=!layout.navOpen;save();apply();},true);
    $(".connection-status").addEventListener("click",event=>{event.stopImmediatePropagation();openModule("outputs");},true);
    $("#log-dock").hidden=true;
    const sidebarGrip=node("div","sidebar-resizer");$(".sidebar").append(sidebarGrip);resize(sidebarGrip,"sidebar","x");
    const rail=$('.status-rail');if(rail)$('#settings-open').before(rail);
  }
  function reorderNavigation(){const nav=$(".primary-nav"),ids=defaults.nav;
    for(const id of [...new Set([...(layout.nav||[]),...ids])]){if(ids.includes(id))nav.append($('.nav-item[data-view="'+id+'"]'));}
    function ordered(){return [...nav.children].filter(n=>ids.includes(n.dataset.view));}
    function store(){layout.nav=ordered().map(n=>n.dataset.view);save();}
    let dragged=null,moved=false;
    for(const item of ordered()){
      if(paths[item.dataset.view])item.querySelector(".nav-icon").innerHTML=icon(item.dataset.view);
      item.draggable=true;item.addEventListener("dragstart",event=>{dragged=item;moved=true;event.dataTransfer.setData("application/x-cobot-navigation",item.dataset.view);event.dataTransfer.effectAllowed="move";item.classList.add("nav-dragging");});
      item.addEventListener("dragend",()=>{item.classList.remove("nav-dragging");dragged=null;setTimeout(()=>{moved=false;},0);});
      item.addEventListener("click",event=>{if(moved){event.preventDefault();event.stopImmediatePropagation();}},true);
      item.addEventListener("dragover",event=>{if(dragged&&dragged!==item){event.preventDefault();item.classList.add("nav-drop-target");}});
      item.addEventListener("dragleave",()=>item.classList.remove("nav-drop-target"));
      item.addEventListener("drop",event=>{event.preventDefault();item.classList.remove("nav-drop-target");const source=$('.nav-item[data-view="'+event.dataTransfer.getData("application/x-cobot-navigation")+'"]');if(!source||source===item||!ids.includes(source.dataset.view))return;const before=new Map(ordered().map(n=>[n,n.getBoundingClientRect()]));const rect=item.getBoundingClientRect();nav.insertBefore(source,event.clientY>rect.top+rect.height/2?item.nextSibling:item);for(const n of ordered()){const old=before.get(n),now=n.getBoundingClientRect();if(old&&old.top!==now.top)n.animate([{transform:'translateY('+(old.top-now.top)+'px)'},{transform:'none'}],{duration:180,easing:'ease-out'});}store();});
      item.addEventListener("keydown",event=>{if(!event.altKey||!['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const items=ordered(),at=items.indexOf(item),target=items[at+(event.key==='ArrowDown'?1:-1)];if(target){nav.insertBefore(item,event.key==='ArrowDown'?target.nextSibling:target);store();item.focus();}});
    }
  }
  function positionSettings(){
    const drawer=$('#settings-drawer');if(!drawer||drawer.hidden)return;
    const anchor=$('#settings-open').getBoundingClientRect(),left=$('.sidebar').getBoundingClientRect().right+8;
    drawer.style.setProperty('--settings-left',Math.min(left,Math.max(8,innerWidth-320))+'px');
    drawer.style.setProperty('--settings-bottom',Math.max(10,innerHeight-anchor.bottom)+'px');
    if(settingsHostPanel&&!settingsHostPanel.hidden){const rect=drawer.getBoundingClientRect(),available=innerWidth-rect.right-20;const width=Math.min(510,available>=300?available:innerWidth-left-12);settingsHostPanel.style.width=Math.max(240,width)+'px';settingsHostPanel.style.left=(available>=300?rect.right+8:left)+'px';settingsHostPanel.style.bottom=Math.max(10,innerHeight-anchor.bottom)+'px';}
  }
  function settingsHost(){
    const drawer=$('#settings-drawer'),host=$('[data-page="host"]'),trigger=$('#settings-open');
    host.classList.add('settings-host');settingsHostPanel=node('aside','host-flyout');settingsHostPanel.hidden=true;
    const heading=node('header','settings-head');heading.append(node('h2','','本机参数'));
    const closeHost=()=>{settingsHostPanel.hidden=true;host.classList.remove('active');open.setAttribute('aria-expanded','false');};
    heading.append(button('关闭本机参数',closeHost,'close'));settingsHostPanel.append(heading,host);document.body.append(settingsHostPanel);
    function setOpen(visible){clearTimeout(settingsCloseTimer);if(visible){drawer.hidden=false;drawer.classList.remove('is-closing');trigger.setAttribute('aria-expanded','true');positionSettings();}else{closeHost();drawer.classList.add('is-closing');trigger.setAttribute('aria-expanded','false');settingsCloseTimer=setTimeout(()=>{drawer.hidden=true;drawer.classList.remove('is-closing');},160);}}
    const section=node('section','host-settings-entry'),open=button('本机参数　›',()=>{const show=settingsHostPanel.hidden;settingsHostPanel.hidden=!show;host.classList.toggle('active',show);open.setAttribute('aria-expanded',String(show));positionSettings();if(show)window.CobotWorkspaceUI.onPage('host');});
    section.append(open);drawer.querySelector('.settings-head').after(section);
    trigger.addEventListener('click',event=>{event.stopImmediatePropagation();setOpen(drawer.hidden||drawer.classList.contains('is-closing'));},true);
    $('#settings-close').addEventListener('click',event=>{event.stopImmediatePropagation();setOpen(false);},true);
    $('.nav-item[data-view=host]').addEventListener('click',event=>{event.stopImmediatePropagation();setOpen(true);if(settingsHostPanel.hidden)open.click();},true);
    document.addEventListener('pointerdown',event=>{if(!drawer.hidden&&!drawer.contains(event.target)&&!settingsHostPanel.contains(event.target)&&!trigger.contains(event.target))setOpen(false);});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!drawer.hidden){event.stopPropagation();setOpen(false);trigger.focus();}},true);
    const reset=[...drawer.querySelectorAll('button')].find(b=>b.textContent==='恢复默认位置');reset?.addEventListener('click',()=>{try{localStorage.removeItem(key);}catch(_){}},true);
  }
  function applyCommands(){
    const output=modules.outputs;if(!output||!layout)return;
    const side=['left','right'].includes(layout.modules.outputs)||output.clientWidth<660;
    output.classList.toggle('commands-stacked',side);output.classList.toggle('commands-hidden',!layout.commands.visible||output.clientHeight<180);
    output.style.setProperty('--command-size',Math.min(side?Math.max(65,output.clientHeight-154):Math.max(200,output.clientWidth*.6),side?layout.commands.height:layout.commands.width)+'px');
    const toggle=$('#output-command-toggle');if(toggle){toggle.classList.toggle('selected',layout.commands.visible);toggle.setAttribute('aria-expanded',String(layout.commands.visible));}
    const grip=$('#command-resizer');if(grip)grip.setAttribute('aria-orientation',side?'horizontal':'vertical');
  }
  function commandResize(grip){
    grip.tabIndex=0;grip.setAttribute('role','separator');grip.setAttribute('aria-label','调整命令区大小');
    const stacked=()=>modules.outputs.classList.contains('commands-stacked');
    function set(value){layout.commands[stacked()?'height':'width']=Math.max(stacked()?85:200,value);applyCommands();}
    grip.addEventListener('pointerdown',event=>{if(event.button!==0)return;event.preventDefault();const side=stacked(),start=parseFloat(modules.outputs.style.getPropertyValue('--command-size')),at=side?event.clientY:event.clientX;document.body.classList.add('dock-resizing');const move=e=>set(start+at-(side?e.clientY:e.clientX));const end=()=>{document.removeEventListener('pointermove',move,true);document.removeEventListener('pointerup',end,true);document.removeEventListener('pointercancel',end,true);document.body.classList.remove('dock-resizing');save();};document.addEventListener('pointermove',move,true);document.addEventListener('pointerup',end,true);document.addEventListener('pointercancel',end,true);});
    grip.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();set(parseFloat(modules.outputs.style.getPropertyValue('--command-size'))+(['ArrowLeft','ArrowUp'].includes(event.key)?20:-20));save();});
    grip.addEventListener('dblclick',()=>{set(stacked()?220:350);save();});
  }
  function buildOutput(){const output=node("section","task-output");output.id="task-output";
    const toolbar=node("div","task-output-toolbar"),select=node("select","output-task-select");select.id="output-task-select";select.setAttribute("aria-label","选择任务");select.addEventListener("change",()=>selectTask(select.value));
    const meta=node("span","output-task-meta");meta.id="output-task-meta";const history=node("label","output-history");const check=node("input");check.type="checkbox";check.id="output-history-check";check.addEventListener("change",refreshOutputs);history.append(check,document.createTextNode("历史"));
    const auto=button("跟随输出",()=>{follow=!follow;auto.classList.toggle("selected",follow);if(follow)$('#task-log').scrollTop=$('#task-log').scrollHeight;});auto.classList.add("selected");
    const stop=button("终止任务",stopTask);stop.id="output-stop";stop.classList.add("danger");stop.disabled=true;
    const commandToggle=button('命令',()=>{layout.commands.visible=!layout.commands.visible;save();applyCommands();});commandToggle.id='output-command-toggle';
    const rawLabel=node("label","output-history"),rawCheck=node("input");rawCheck.type="checkbox";rawCheck.id="output-raw-check";rawCheck.addEventListener("change",()=>{lastOutput="";renderOutput();});rawLabel.append(rawCheck,document.createTextNode(window.CobotPreferences?.language==="en"?"Raw log":"原始日志"));
    toolbar.append(select,meta,history,rawLabel,auto,button("刷新输出",refreshOutputs,"refresh"),commandToggle,stop);
    const body=node("div","task-output-body"),tasks=node("nav","output-task-list"),log=node("pre","task-log");tasks.id="output-task-list";tasks.setAttribute("aria-label","任务列表");log.id="task-log";log.tabIndex=0;log.textContent=window.CobotPreferences.text("等待任务输出");body.append(tasks,log);
    const commands=node("section","output-commands"),bar=node("div","command-bar"),groups=node('select','command-group-select');groups.id='command-group-select';groups.setAttribute('aria-label','命令任务分类');groups.addEventListener('change',()=>{commandGroup=groups.value;commandChoice='current';renderCommands();});
    const options=node("select","common-command-select");options.id="common-command-select";options.setAttribute("aria-label","选择命令");options.addEventListener("change",()=>{commandChoice=options.value;renderCommands();});
    const close=button('隐藏命令',()=>{layout.commands.visible=false;save();applyCommands();},'close');
    bar.append(groups,button("复制命令",()=>copy($('#output-command-text').textContent),"copy"),close);const code=node("pre","output-command-text");code.id="output-command-text";
    const path=node("div","output-log-path");path.id="output-log-path";commands.append(bar,options,code,path);
    const grip=node('div','command-resizer');grip.id='command-resizer';commandResize(grip);
    const feedback=node("div","output-feedback");feedback.id="output-feedback";feedback.setAttribute("role","status");output.append(toolbar,body,grip,commands,feedback);modules.outputs=output;
    output.addEventListener('keydown',event=>{if(event.target.closest('input,select,textarea'))return;if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))event.stopPropagation();});
  }
  function task(){return snapshot?.tasks.find(row=>String(row.id)===selectedTask);}
  function selectTask(id){selectedTask=id;selectedComponent=task()?.component||"";commandGroup=groupFor(selectedComponent);commandChoice='current';lastOutput="";renderOutput();}
  function followTask(job){if(!job)return;pendingTask={id:String(job.job_id||job.id||''),component:job.component||'deployment'};commandGroup=groupFor(pendingTask.component);commandChoice='current';lastOutput='';follow=true;openModule('outputs');refreshOutputs();}
  function renderOutput(){if(!snapshot)return;const jobs=snapshot.tasks;
    if(pendingTask){const found=jobs.find(j=>pendingTask.id?String(j.id)===pendingTask.id:j.component===pendingTask.component);if(found){selectedTask=String(found.id);selectedComponent=found.component;pendingTask=null;}}
    if(!jobs.some(j=>String(j.id)===selectedTask))selectedTask=String((jobs.find(j=>j.component===selectedComponent)||jobs.find(j=>j.can_stop)||jobs[0])?.id||"");
    const signature=JSON.stringify(jobs.map(j=>[j.id,j.component,j.phase,j.pid]));
    if(signature!==lastTasks){lastTasks=signature;const list=$('#output-task-list'),select=$('#output-task-select');list.replaceChildren();select.replaceChildren();for(const job of jobs){const label=names[job.component]||job.component;const item=button(label,()=>selectTask(String(job.id)));item.className="output-task";item.dataset.task=job.id;const dot=node('i','task-status-dot');dot.dataset.phase=job.phase;item.append(dot,node('small','',phase[job.phase]||job.phase));list.append(item);const option=node('option','',label+' · '+(phase[job.phase]||job.phase));option.value=job.id;select.append(option);}}
    $('#output-task-select').value=selectedTask;for(const item of $$('.output-task'))item.classList.toggle('selected',item.dataset.task===selectedTask);
    const current=task(),log=$('#task-log'),text=(!$('#output-raw-check')?.checked&&current?.important_output?.[window.CobotPreferences?.language==='en'?'en':'zh'])||current?.log_tail||window.CobotPreferences.text(current?.detail||(current?'暂无输出':'暂无任务'));
    if(lastOutput!==text){const top=log.scrollTop;log.textContent=text;lastOutput=text;log.scrollTop=follow?log.scrollHeight:top;}
    const age=snapshot.updated_at?new Date(snapshot.updated_at*1000).toLocaleTimeString():'';
    $('#output-task-meta').textContent=current?(names[current.component]||current.component)+' · '+(phase[current.phase]||current.phase)+' · PID '+(current.pid||'—')+' · '+age:'暂无任务';
    $('#output-stop').disabled=stopping||!current?.can_stop||current.phase==='stopping';$('#output-stop').textContent=current?.stop_kind==='model'?'释放模型组':current?.phase==='stopping'?'停止中':'终止任务';
    renderCommands();
  }
  function groupFor(component){return ({arms:'机械臂',cameras:'相机',can:'CAN',roscore:'ROS',home:'归位',pose:'位姿',recover:'恢复',rlt:'部署',deployment:'部署',stage1:'部署',pi05:'部署'})[component]||'诊断';}
  function renderCommands(){if(!snapshot)return;const job=task(),groups=$('#command-group-select'),select=$('#common-command-select');const catalog=snapshot.commands||[],labels=[...new Set(catalog.map(c=>c.group))];if(!commandGroup)commandGroup=groupFor(job?.component);if(!labels.includes(commandGroup))labels.unshift(commandGroup);
    const groupSignature=JSON.stringify(labels);if(groups.dataset.signature!==groupSignature){groups.replaceChildren(...labels.map(label=>{const o=node('option','',label);o.value=label;return o;}));groups.dataset.signature=groupSignature;}groups.value=commandGroup;
    const entries=catalog.map((cmd,index)=>({...cmd,id:String(index)})).filter(cmd=>cmd.group===commandGroup);
    if(job&&groupFor(job.component)===commandGroup){
      const choices=[{id:'current',label:'本次执行 · '+(names[job.component]||job.component)}];
      if(job.terminal_command)choices.push({id:'process',label:'实际启动与进程'});
      entries.unshift(...choices);
    }
    if(!entries.some(c=>c.id===commandChoice))commandChoice=entries[0]?.id||'';
    const signature=JSON.stringify(entries.map(c=>[c.id,c.label]));if(select.dataset.signature!==signature){select.replaceChildren(...entries.map(cmd=>{const o=node('option','',cmd.label);o.value=cmd.id;return o;}));select.dataset.signature=signature;}select.value=commandChoice;
    const recipe=entry=>{if(!entry)return window.CobotPreferences.text('暂无命令');const lang=window.CobotPreferences?.language==='en'?'en':'zh';const note=entry.implementation?.[lang];return [entry.terminal_command||entry.command,note&&note.split('\n').map(line=>'# '+line).join('\n')].filter(Boolean).join('\n\n');};
    let text='';
    if(commandChoice!=='current'&&commandChoice!=='process')text=recipe(entries.find(c=>c.id===commandChoice));
    else if(job&&commandChoice==='current'&&job.terminal_command)text=recipe(job);
    else if(job){const chunks=[];if(job.cwd_command||job.cwd)chunks.push(job.cwd_command||('cd '+job.cwd));if(job.command_text)chunks.push(window.CobotPreferences.text('# 启动命令')+'\n'+job.command_text);if(job.process_command&&job.process_command!==job.command_text)chunks.push(window.CobotPreferences.text('# 当前进程')+'\n'+job.process_command);if(job.stop_command)chunks.push(window.CobotPreferences.text('# 核对 PID 后停止')+'\n'+job.stop_command);text=chunks.join('\n\n')||window.CobotPreferences.text('未登记启动命令');}
    const code=$('#output-command-text');if(code.textContent!==text)code.textContent=text;$('#output-log-path').textContent=['current','process'].includes(commandChoice)&&job?.log_path?'日志：'+job.log_path:'';
  }
  async function refreshOutputs(){if(fetching||!initialized)return;fetching=true;try{snapshot=await json('/api/console/outputs?history='+$('#output-history-check').checked);renderOutput();}catch(error){$('#output-feedback').textContent=error.message;}finally{fetching=false;}}
  async function stopTask(){const job=task();if(stopping||!job?.can_stop)return;const label=names[job.component]||job.component;const msg=job.stop_kind==='model'?'停止并释放当前模型组？':'终止 '+label+'（PID '+job.pid+'）？';if(!confirm(window.CobotPreferences.text(msg)))return;stopping=true;renderOutput();report('正在停止 '+label);try{await json('/api/console/outputs/stop',{id:String(job.id),component:job.component,pid:job.pid,start_ticks:job.start_ticks,model_pid:job.model_pid||null,model_start_ticks:job.model_start_ticks||null});report('停止请求已提交，等待进程退出');}catch(error){report(error.message,true);}finally{stopping=false;await refreshOutputs();}}
  function init(){if(initialized)return;layout=restore();for(const name of Object.keys(defaults.modules))if(!edges.includes(layout.modules[name]))layout.modules[name]=defaults.modules[name];buildOutput();buildShell();reorderNavigation();settingsHost();initialized=true;document.body.classList.add('workbench-docked');apply();save();new ResizeObserver(fitDocks).observe(shell);window.addEventListener('resize',apply);window.CobotOutputPanel={refresh:refreshOutputs,follow:followTask,visible:()=>moduleVisible('outputs')};window.CobotDockUI={place,open:openModule,get layout(){return JSON.parse(JSON.stringify(layout));}};setInterval(()=>{if(!document.hidden&&moduleVisible('outputs'))refreshOutputs();},1000);}
  document.addEventListener('cobot:language',()=>{if(initialized)renderOutput();});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
