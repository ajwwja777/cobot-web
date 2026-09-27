(function(root) {
  "use strict";
  const failures = [];
  const nativeFetch = root.fetch && root.fetch.bind(root);
  const terminal = "cd /home/agilex/jiaan/project/cobot-web\npython3 scripts/console.py recovery status\npython3 scripts/console.py recovery snapshot";
  const text = (zh,en) => root.CobotPreferences?.language === "en" ? en : zh;
  function guidance(status, method, path, detail="") {
    let zh="检查状态和对应任务日志；不要仅凭 HTTP 状态码判断根因。";
    let en="Check current state and the task log; an HTTP status alone does not identify the cause.";
    if (!status) {
      zh="检查网络和 8015 服务。前端断线不会停止推理；用终端 recovery status 确认现场状态。";
      en="Check the connection and service on port 8015. Browser disconnects do not stop inference; inspect recovery status in a terminal.";
    } else if (status === 409) {
      zh="状态冲突：检查当前 Episode、Session、推理和正在执行的操作，完成必要的暂停或结束步骤。";
      en="State conflict: inspect the active episode, session, inference and pending operation before pausing or ending it.";
    } else if (status === 422) {
      zh="检查响应中的参数和接口字段，并核对前后端版本；反复重启不能修复接口不兼容。";
      en="Check the response fields, parameters and client/server versions. Restarts cannot fix an incompatible API.";
    } else if (status >= 500) {
      zh="检查模型服务、录制器和原始日志。503 可能是服务不可达，也可能是保存流程或接口错误。";
      en="Inspect model services, the recorder and original logs. A 503 can mean an unavailable service or a finalization/API error.";
    } else if (status === 404) {
      zh="确认服务版本、模型或记录是否存在，以及当前数据目录是否正确。";
      en="Verify the service version, requested model/record and selected data directory.";
    }
    if (/input.output error|read.only file system|no space left|disk_full/i.test(detail)) {
      zh="存储读写异常：停止新增采集与写入，检查目标盘、剩余空间和系统日志。不要反复重启或热拔正在使用的磁盘。";
      en="Storage I/O failure: stop new recording/writes and inspect the target disk, free space and kernel logs. Do not repeatedly restart or unplug a disk in use.";
    }
    const uncertain = !["GET","HEAD","OPTIONS"].includes(method);
    if (uncertain) {
      zh+=" 此请求结果待确认，可能已部分完成；先查记录，不要重复开始、保存或标注。";
      en+=" The outcome is unconfirmed and may be partially applied. Inspect records before repeating start, save or label actions.";
    }
    const task = /deployment|collection\/model/.test(path) ? "model" : "web";
    return {zh,en,uncertain,command:terminal+"\npython3 scripts/console.py recovery logs "+task};
  }
  function redact(value) {
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(
      ([key,item]) => [key,/password|token|secret/i.test(key) ? "[redacted]" : redact(item)]));
    return value;
  }
  function record(item) {
    const key=[item.method,item.path,item.status,item.detail].join("|");
    const previous=failures.find(row=>row.key===key);
    if(previous){previous.time=new Date().toISOString();previous.count++;failures.splice(failures.indexOf(previous),1);failures.unshift(previous);}
    else failures.unshift({...item,key,time:new Date().toISOString(),count:1});
    failures.splice(20);
    render();
  }
  if (nativeFetch && root.location && typeof document !== "undefined") root.fetch = async function(input, options={}) {
    const address=typeof input==="string" ? input : (input instanceof URL ? String(input) : input.url);
    const url=new URL(address,root.location.href);
    const method=String(options.method || input.method || "GET").toUpperCase();
    const tracked=url.origin===root.location.origin && url.pathname.startsWith("/api/")
      && !/\.(?:jpe?g|png|mjpeg|mjpg|mp4)$/.test(url.pathname);
    if(!tracked)return nativeFetch(input,options);
    const controller=!(options.signal || input.signal) ? new AbortController() : null;
    const timer=controller ? setTimeout(()=>controller.abort(),method==="GET"?15000:120000) : null;
    try {
      const response=await nativeFetch(input,controller?{...options,signal:controller.signal}:options);
      if(!response.ok) {
        let detail="";
        try{const raw=await response.clone().text();try{detail=JSON.stringify(redact(JSON.parse(raw)));}catch(_){detail=raw;}}catch(_){}
        record({path:url.pathname,method,status:response.status,detail:detail.slice(0,3000)});
      }
      return response;
    } catch(error) {
      record({path:url.pathname,method,status:0,detail:error.name==="AbortError"?
        "Request interrupted / timeout":String(error.message||error)});
      throw error;
    } finally {if(timer)clearTimeout(timer);}
  };
  let dialog, button, history, health, lastProbe=[], probing=false;
  function node(tag,content,cls) {
    const value=document.createElement(tag);
    if(content!=null)value.textContent=content;
    if(cls)value.className=cls;
    return value;
  }
  function render() {
    if(!button)return;
    button.textContent=text("诊断","Diagnostics")+(failures.length?" · "+failures.length:"");
    button.classList.toggle("has-errors",failures.length>0);
    if(!dialog.open)return;
    dialog.querySelector("h3").textContent=text("连接、状态与排障","Connection, status and recovery");
    dialog.querySelector("[data-refresh]").textContent=text("检查当前状态","Check current state");
    dialog.querySelector("[data-clear]").textContent=text("清除错误列表","Clear error list");
    dialog.querySelector("[data-manual]").textContent=text("命令行操作手册","Command-line manual");
    history.replaceChildren();
    if(!failures.length)history.append(node("p",text("尚未捕获接口错误；这不代表设备已就绪。","No API errors captured; this does not imply hardware readiness.")));
    for(const row of failures) {
      const item=node("article",null,"recovery-error");
      const help=guidance(row.status,row.method,row.path,row.detail);
      item.append(node("strong",row.method+" "+row.path+" · "+(row.status?"HTTP "+row.status:text("连接中断","Disconnected"))),
        node("small",row.time+" · "+row.count),
        node("pre",row.detail),node("p",text(help.zh,help.en)),node("pre",help.command));
      history.append(item);
    }
    health.replaceChildren();
    for(const row of lastProbe) {
      const details=node("details",null,"recovery-probe");
      details.append(node("summary",row.label+" · "+(row.ok ? row.summary : text("不可用 / 状态未知","Unavailable / state unknown"))));
      if(row.path.endsWith("/devices")) {
        for(const [name,value] of Object.entries(row.payload?.systems?.device_health||{})) {
          details.append(node("p",name+" · "+text(value.reason_zh,value.reason_en)+" "+text(value.remedy_zh,value.remedy_en)));
        }
      }
      if(!row.ok) {
        const help=guidance(row.status||0,"GET",row.path,JSON.stringify(row.payload));
        details.append(node("p",text(help.zh,help.en)));
      }
      details.append(node("pre",JSON.stringify(redact(row.payload),null,2)));
      health.append(details);
    }
  }
  const probes=[
    ["8015","/api/console/identity"],["Devices / CAN / ROS","/api/console/devices"],
    ["Cameras","/api/console/cameras"],["Collection","/api/console/status"],
    ["Model","/api/deployment/status"],["Recorder","/api/rlt/recorder-diagnostics"],
    ["Storage / host","/api/console/host"]
  ];
  function summarize(path,payload) {
    if(path.endsWith("/devices"))return Object.entries(payload.systems||{}).filter(([k,v])=>v&&v.phase)
      .map(([k,v])=>k+": "+v.phase).join(" · ")||"HTTP 200";
    if(path.endsWith("/status")&&path.includes("/console/"))return [
      "capture: "+payload.capture_phase,"recorder: "+payload.recorder_state,
      "ROS: "+(payload.ros_readiness?.status||"unknown"),"RLT: "+payload.rlt_backend_phase].join(" · ");
    if(path.endsWith("/host"))return payload.disk ? (payload.disk.free_bytes/1024**3).toFixed(1)+" GiB · "+payload.disk.path : "HTTP 200";
    return String(payload.phase||payload.state||payload.status||payload.service||"HTTP 200")+
      (payload.error?" · "+payload.error:"");
  }
  async function probe() {
    if(probing||!nativeFetch)return;probing=true;
    try{
      lastProbe=await Promise.all(probes.map(async([label,path])=>{
        const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
        try{
          const response=await nativeFetch(path,{cache:"no-store",signal:controller.signal});
          const payload=await response.json();
          return {label,path,ok:response.ok,status:response.status,payload,summary:summarize(path,payload)};
        }catch(error){return {label,path,ok:false,payload:{error:String(error.message||error)}};}
        finally{clearTimeout(timer);}
      }));
      render();
    }finally{probing=false;}
  }
  function mount() {
    if(button)return;
    const toolbar=document.querySelector(".task-output-toolbar");
    if(!toolbar)return;
    button=node("button",text("诊断","Diagnostics"),"ghost recovery-open");
    button.type="button";button.id="console-recovery-open";toolbar.append(button);
    dialog=node("dialog",null,"recovery-dialog");dialog.id="console-recovery-dialog";
    const head=node("header"),close=node("button","×","ghost");
    close.type="button";close.setAttribute("aria-label",text("关闭","Close"));
    close.onclick=()=>dialog.close();head.append(node("h3"),close);
    const actions=node("div",null,"recovery-actions");
    const refresh=node("button"),clear=node("button"),manual=node("a");
    refresh.type=clear.type="button";refresh.dataset.refresh="";clear.dataset.clear="";manual.dataset.manual="";
    refresh.onclick=probe;clear.onclick=()=>{failures.splice(0);render();};
    manual.href="https://github.com/ajwwja777/cobot-web/blob/main/docs/COMMAND_LINE.md";
    manual.target="_blank";manual.rel="noopener";
    actions.append(refresh,clear,manual);
    health=node("section");history=node("section");
    dialog.append(head,actions,node("pre",terminal),health,history);document.body.append(dialog);
    button.onclick=()=>{dialog.showModal();render();probe();};
    document.addEventListener("cobot:language",render);
    render();
  }
  const api={guidance,redact,record,failures,probe,mount,summarize};
  root.CobotTroubleshooting=api;
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  if(typeof document!=="undefined"){
    if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",()=>setTimeout(mount,0));
    else mount();
  }
})(typeof window!=="undefined"?window:globalThis);
