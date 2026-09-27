"use strict";
(function dashboardLocale(root){
  const textSources=new WeakMap(),attributeSources=new WeakMap(),queued=new Set();
  let scheduled=false,mounted=false;
  const attributes=['title','aria-label','alt','placeholder'];
  const language=()=>root.CobotPreferences?.language==='en'?'en':'zh';
  const translate=(raw,lang=language())=>lang==='en'?root.CobotLocaleCatalog.english(raw):raw;
  function excluded(element){
    return Boolean(element?.closest('script,style,[data-i18n-skip],.compatibility-nodes')||
      (element?.closest('pre,code')&&!element.closest('[data-localize]')));
  }
  function updateText(node){
    const parent=node.parentElement;if(!parent||excluded(parent))return;
    const previous=textSources.get(node);
    let raw=previous?.shown===node.data?previous.raw:node.data;
    const zh=parent.dataset?.zh,en=parent.dataset?.en;
    const annotated=zh!=null&&en!=null&&(raw===zh||raw===en);
    if(annotated)raw=zh;
    const shown=annotated?(language()==='en'?en:zh):translate(raw);
    textSources.set(node,{raw,shown});
    if(node.data!==shown)node.data=shown;
  }
  function updateAttributes(element){
    if(excluded(element))return;
    const saved=attributeSources.get(element)||{};
    for(const key of attributes){
      const current=element.getAttribute(key);if(current===null)continue;
      const old=saved[key];let raw=old?.shown===current?old.raw:current;
      const suffix=key==='aria-label'?'aria':key;
      const zh=element.getAttribute('data-zh-'+suffix),en=element.getAttribute('data-en-'+suffix);
      const annotated=zh!==null&&en!==null&&(raw===zh||raw===en);
      if(annotated)raw=zh;
      const shown=annotated?(language()==='en'?en:zh):translate(raw);
      saved[key]={raw,shown};if(shown!==current)element.setAttribute(key,shown);
    }
    attributeSources.set(element,saved);
  }
  function walk(node){
    if(!node)return;
    if(node.nodeType===3){updateText(node);return;}
    if(node.nodeType!==1||excluded(node))return;
    updateAttributes(node);
    const walker=document.createTreeWalker(node,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT,{
      acceptNode:item=>item.nodeType===1&&excluded(item)?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT,
    });
    while(walker.nextNode()){
      const item=walker.currentNode;
      if(item.nodeType===3)updateText(item);else updateAttributes(item);
    }
  }
  function flush(){
    scheduled=false;
    const nodes=[...queued];queued.clear();
    const roots=nodes.filter(node=>!nodes.some(other=>other!==node&&other.nodeType===1&&other.contains(node)));
    for(const node of roots)if(node.isConnected)walk(node);
  }
  function mount(){
    if(mounted||typeof document==='undefined')return;mounted=true;
    walk(document.body);
    document.addEventListener('cobot:language',()=>walk(document.body));
    new MutationObserver(records=>{
      for(const record of records){
        if(record.type==='childList')for(const node of record.addedNodes)queued.add(node);
        else queued.add(record.target);
      }
      if(queued.size&&!scheduled){scheduled=true;requestAnimationFrame(flush);}
    }).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:attributes});
  }
  root.CobotDashboardLocale={mount,translate,walk};
  if(typeof document!=='undefined')document.readyState==='loading'?document.addEventListener('DOMContentLoaded',mount):mount();
})(typeof globalThis!=='undefined'?globalThis:this);
