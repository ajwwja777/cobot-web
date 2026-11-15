"use strict";
(() => {
 const host=window.CobotMotionUI?.replayMode;if(!host?.add)return;
 const el=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
 const text=(zh,en)=>window.CobotPreferences?.language==="en"?en:zh;
 const panel=el("article");panel.className="panel motion-panel";panel.id="video-download-panel";
 panel.append(el("h3",text("视频下载","Download videos")));
 const root=el("input");root.id="video-download-root";root.spellcheck=false;root.value=document.getElementById("deploy-video-root")?.value||"/media/agilex/Getea1/jiaan/data/videos/deployment";
 const label=el("label"),caption=el("span",text("视频保存目录","Video directory"));label.append(caption,root);
 const row=el("div");row.className="motion-inline-row";
 const select=el("select");select.id="video-download-record";select.setAttribute("aria-label",text("已保存的视频","Saved videos"));
 const refresh=el("button",text("刷新","Refresh"));refresh.id="video-download-refresh";refresh.type="button";row.append(select,refresh);
 const info=el("p"),files=el("div"),status=el("p");info.id="video-download-info";files.id="video-download-files";files.className="video-download-files";status.id="video-download-status";status.className="inline-status";status.setAttribute("role","status");
 panel.append(label,row,info,files,status);
 let records=[],chosen="",generation=0,controller=null;
 const names={camera_left:text("左腕","Left wrist"),camera_high:text("中路","Middle"),camera_right:text("右腕","Right wrist")};
 function size(bytes){const units=["B","KiB","MiB","GiB"];let value=Number(bytes)||0,i=0;while(value>=1024&&i<3){value/=1024;i++;}return value.toFixed(i?2:0)+" "+units[i];}
 function render(){
  files.replaceChildren();const record=records.find(item=>item.id===chosen);
  if(!record){info.textContent="";return;}
  info.textContent=(record.state==="saved"?text("已保存","Saved"):text("未完整结束","Incomplete"))+" · "+text("总计 ","Total ")+size(record.total_bytes);
  for(const file of record.files||[]){
   let url;try{url=new URL(file.download_url,location.href);}catch(_){continue;}
   if(url.origin!==location.origin||url.pathname!=="/api/videos/file"||!names[file.camera])continue;
   const card=el("div");card.className="video-download-card";
   if(file.playable){const video=el("video");video.controls=true;video.preload="metadata";video.playsInline=true;video.src=url.pathname+url.search+"&download=false";video.setAttribute("aria-label",names[file.camera]);card.append(video);}
   const link=el("a",text("下载 ","Download ")+names[file.camera]+text("视频"," video")+" · "+size(file.bytes)+(file.incomplete?text(" · 不完整"," · incomplete"):""));
   link.href=url.pathname+url.search;link.download="";link.className="video-download-link";link.dataset.camera=file.camera;card.append(link);files.append(card);
  }
  if(!files.children.length)status.textContent=text("录像尚未收尾，完成后刷新。","Recording has not finalized; refresh after it finishes.");
 }
 async function load(){
  files.querySelectorAll("video").forEach(video=>video.pause());files.replaceChildren();info.textContent="";
  const token=++generation;controller?.abort();controller=new AbortController();const activeController=controller,timer=setTimeout(()=>activeController.abort(),12000);refresh.disabled=true;status.textContent=text("正在读取视频…","Reading videos…");
  try{
   const response=await fetch("/api/videos/records?data_root="+encodeURIComponent(root.value.trim()),{cache:"no-store",signal:activeController.signal});const data=await response.json();
   if(!response.ok)throw Error(typeof data.detail==="string"?data.detail:JSON.stringify(data.detail||response.status));if(token!==generation)return;
   records=data.records||[];select.replaceChildren(...records.map(record=>{const option=el("option",new Date(record.saved_at*1000).toLocaleString()+" · "+record.id);option.value=record.id;return option;}));
   if(records.some(record=>record.id===chosen))select.value=chosen;else chosen=select.value;
   if(!records.length)select.append(el("option",text("暂无视频","No videos")));
   status.textContent=records.length?text("MP4 可直接播放或下载。","MP4 videos can be played or downloaded."):text("此目录暂无视频。","No videos in this directory.");render();
  }catch(error){if(token!==generation)return;status.textContent=error.name==="AbortError"?text("读取超时，请重试。","Read timed out; retry."):error.message;}
  finally{clearTimeout(timer);if(token===generation)refresh.disabled=false;}
 }
 select.addEventListener("change",()=>{chosen=select.value;status.textContent="";render();});refresh.addEventListener("click",load);root.addEventListener("change",()=>{chosen="";load();});
 host.add("videos",text("视频下载","Videos"),panel,load);
 window.CobotVideoDownloads={refresh:load};
})();
