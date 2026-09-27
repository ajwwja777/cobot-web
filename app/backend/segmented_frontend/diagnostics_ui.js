"use strict";

(function expose(root) {
  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function formatExplanation(value) {
    const item = value || {};
    if (item.code === "waiting_for_episodes") {
      return {
        tone: "waiting",
        title: item.missing > 0 ? "还差 " + item.missing + " 条即可更新" : "已满足更新批量",
        detail: "当前 " + item.pending + "/" + item.required + " 条从未尝试且已验证的 episode；"
          + item.quarantined + " 条已隔离样本保留审计，不会自动重试。",
      };
    }
    if (item.code === "update_preparing") return {
      tone: "active", title: "正在准备训练数据",
      detail: "新数据 " + item.transitions + " transitions，随后计划 " + item.updates + " updates。",
    };
    if (item.code === "update_training") return {
      tone: "active", title: "正在更新 actor / critic",
      detail: "本轮 " + item.transitions + " transitions，计划 " + item.updates + " updates。",
    };
    if (item.code === "candidate_accepted") return {
      tone: "good", title: "最近候选已通过离线门限",
      detail: "由 " + item.episodes + " 条新 episode 生成，已发布给下一轮 Session。",
    };
    if (item.code === "candidate_rejected") return {
      tone: "warn", title: "最近候选被离线门限拒绝",
      detail: item.episodes + " 条 episode 保留审计但候选未发布，不会阻塞后续新批次。",
    };
    return { tone: "muted", title: "暂无在线更新状态", detail: "等待 RLT 后端或学习账本就绪。" };
  }
  function series(rows, key, xKey, when) {
    const xName = xKey || "global_step";
    const result = [];
    for (const row of Array.isArray(rows) ? rows : []) {
      if (when && !when(row)) continue;
      const x = finite(row[xName]), y = finite(row[key]);
      if (x !== null && y !== null) result.push({ x: x, y: y });
    }
    return result.sort((a, b) => a.x - b.x);
  }
  // Telemetry files append across episodes and service restarts, so the decision counter restarts
  // and the actor changes; plotting all rows against decision draws lines back across the chart.
  function latestRun(rows, key, versionKey) {
    const list = Array.isArray(rows) ? rows : [], xName = key || "decision", vName = versionKey || "actor_version";
    let start = list.length - 1;
    while (start > 0) {
      const previous = list[start - 1], current = list[start];
      if (!(finite(previous[xName]) < finite(current[xName])) || previous[vName] !== current[vName]) break;
      start--;
    }
    return start < 0 ? [] : list.slice(start);
  }
  const actorUpdated = row => Number(row.did_actor_update) === 1;
  function actorUpdateNote(last) {
    if (!last) return "暂无训练指标。";
    if (Number(last.did_actor_update) === 0) return "本步未更新 actor；critic-only 步出现 actor loss=0 属于正常节奏。";
    const loss = finite(last.actor_loss);
    return loss === null ? "本步更新了 actor。" : "本步更新 actor，loss " + loss.toFixed(4) + "。";
  }
  class MotionBuffer {
    constructor(maximum) { this.maximum = maximum || 120; this.rows = []; this.lastTime = null; }
    push(time, robot) {
      const stamp = finite(time);
      if (stamp === null || stamp === this.lastTime) return false;
      const front = robot && robot.front_right;
      const velocity = front && Array.isArray(front.velocity) ? front.velocity.slice(0, 6) : [];
      const valid = velocity.map(finite).filter(value => value !== null);
      const maxVelocity = valid.length ? Math.max.apply(null, valid.map(Math.abs)) : null;
      const rms = valid.length ? Math.sqrt(valid.reduce((sum, value) => sum + value * value, 0) / valid.length) : null;
      this.rows.push({ time: stamp, maxVelocity: maxVelocity, velocityRms: rms });
      while (this.rows.length > this.maximum) this.rows.shift();
      this.lastTime = stamp;
      return true;
    }
  }
  function createPoller(load, receive, fail) {
    let busy = false;
    async function tick() {
      if (busy) return false;
      busy = true;
      try { receive(await load()); return true; }
      catch (error) { fail(error); return false; }
      finally { busy = false; }
    }
    return { tick: tick, get busy() { return busy; } };
  }
  // options: xKey, xLabel, yLabel, logY (losses spanning decades), markers [{x,label}] (release boundaries),
  // xFormat (tick formatter). definition.when(row) filters rows per series (e.g. actor-update rows only).
  function chartModel(rows, definitions, options) {
    const opts=options||{}; const groups=[]; const all=[];
    const toY=opts.logY?(y=>y>0?Math.log10(y):null):(y=>y);
    for(const definition of definitions){
      const values=series(rows,definition.key,opts.xKey,definition.when).filter(p=>toY(p.y)!==null);
      groups.push({definition,values});all.push.apply(all,values);
    }
    const yLabel=(opts.yLabel||'')+(opts.logY?' (log)':'');
    if(!all.length)return {empty:true,x:{label:opts.xLabel||'step',ticks:[]},y:{label:yLabel,ticks:[]},series:[],markers:[]};
    let minX=Math.min.apply(null,all.map(p=>p.x)),maxX=Math.max.apply(null,all.map(p=>p.x));
    let minY=Math.min.apply(null,all.map(p=>toY(p.y))),maxY=Math.max.apply(null,all.map(p=>toY(p.y)));
    if(minX===maxX){minX-=1;maxX+=1;} if(minY===maxY){minY-=opts.logY?.5:1;maxY+=opts.logY?.5:1;}
    const pad=(maxY-minY)*.06;minY-=pad;maxY+=pad;
    const ticks=(lo,hi)=>Array.from({length:5},(_,i)=>lo+(hi-lo)*i/4);
    const xTicks=ticks(minX,maxX),yTicks=ticks(minY,maxY);
    const px=x=>38+(x-minX)/(maxX-minX)*254,py=y=>96-(y-minY)/(maxY-minY)*84;
    const plotted=groups.map(group=>{const label=group.definition.label||group.definition.key;const unit=group.definition.unit||'';return {
      key:group.definition.key,label,color:group.definition.color,
      points:group.values.map(p=>({rawX:p.x,rawY:p.y,x:px(p.x),y:py(toY(p.y))})),
      tooltip:index=>label+' · '+group.values[index].x.toFixed(opts.xDigits||0)+' · '+group.values[index].y.toPrecision(4)+(unit?' '+unit:'')
    };});
    const markers=(opts.markers||[]).map(m=>({rawX:finite(m.x),label:String(m.label||'')}))
      .filter(m=>m.rawX!==null&&m.rawX>=minX&&m.rawX<=maxX).map(m=>Object.assign(m,{x:px(m.rawX)}));
    return {empty:false,logY:!!opts.logY,
      x:{label:opts.xLabel||'step',ticks:xTicks,min:minX,max:maxX,format:opts.xFormat||(v=>v.toFixed(0))},
      y:{label:yLabel,ticks:yTicks,min:minY,max:maxY,format:opts.logY?(v=>Math.pow(10,v).toPrecision(2)):(v=>v.toPrecision(3))},
      series:plotted,markers};
  }
  function svgNode(name,attrs,text){const node=document.createElementNS('http://www.w3.org/2000/svg',name);for(const key of Object.keys(attrs||{}))node.setAttribute(key,String(attrs[key]));if(text!=null)node.textContent=text;return node;}
  function renderChart(svg, rows, definitions, options) {
    while(svg.firstChild)svg.removeChild(svg.firstChild);
    const model=chartModel(rows,definitions,options);
    svg._chartModel=model;
    if(model.empty){svg._chartHover=null;const tip=svg.parentElement&&svg.parentElement.querySelector('.chart-hover-tooltip');if(tip)tip.hidden=true;svg.appendChild(svgNode('text',{x:'50%',y:'52%','text-anchor':'middle'},(options&&options.emptyText)||'暂无数据'));return model;}
    for(let i=0;i<5;i++){const x=38+i*254/4,y=96-i*84/4;svg.appendChild(svgNode('line',{x1:x,y1:8,x2:x,y2:96,class:'chart-gridline'}));svg.appendChild(svgNode('line',{x1:38,y1:y,x2:292,y2:y,class:'chart-gridline'}));svg.appendChild(svgNode('text',{x:x,y:109,'text-anchor':'middle'},model.x.format(model.x.ticks[i])));svg.appendChild(svgNode('text',{x:34,y:y+3,'text-anchor':'end'},model.y.format(model.y.ticks[i])));}
    svg.appendChild(svgNode('text',{x:165,y:119,'text-anchor':'middle',class:'chart-axis-label'},model.x.label));
    svg.appendChild(svgNode('text',{x:10,y:52,transform:'rotate(-90 10 52)','text-anchor':'middle',class:'chart-axis-label'},model.y.label));
    for(const marker of model.markers){const line=svgNode('line',{x1:marker.x,y1:8,x2:marker.x,y2:96,class:'chart-marker'});line.appendChild(svgNode('title',{},marker.label+' · step '+marker.rawX.toFixed(0)));svg.appendChild(line);}
    for(const group of model.series){const points=group.points.map(p=>p.x.toFixed(1)+','+p.y.toFixed(1)).join(' ');svg.appendChild(svgNode('polyline',{points,fill:'none',stroke:group.color,'stroke-width':2,'vector-effect':'non-scaling-stroke'}));group.points.forEach((point,index)=>{const circle=svgNode('circle',{cx:point.x,cy:point.y,r:2.2,fill:group.color,tabindex:0});circle.appendChild(svgNode('title',{},group.tooltip(index)));svg.appendChild(circle);});}
    const vertical=svgNode('line',{x1:0,y1:8,x2:0,y2:96,class:'chart-crosshair'});
    const horizontal=svgNode('line',{x1:38,y1:0,x2:292,y2:0,class:'chart-crosshair'});
    const dot=svgNode('circle',{cx:0,cy:0,r:4,class:'chart-crosshair-point'});
    vertical.style.display=horizontal.style.display=dot.style.display='none';
    svg.append(vertical,horizontal,dot);svg._chartHover={vertical,horizontal,dot};
    if(!svg._chartHoverBound&&typeof svg.addEventListener==='function'){
      svg._chartHoverBound=true;
      svg.addEventListener('pointermove',event=>{
        const current=svg._chartModel,hover=svg._chartHover;if(!current||current.empty||!hover)return;
        const box=svg.getBoundingClientRect(),x=(event.clientX-box.left)*300/box.width,y=(event.clientY-box.top)*120/box.height;
        let best=null;
        for(const group of current.series)for(const point of group.points){const distance=Math.abs(point.x-x)+Math.abs(point.y-y)*.25;if(!best||distance<best.distance)best={group,point,distance};}
        if(!best)return;
        hover.vertical.setAttribute('x1',best.point.x);hover.vertical.setAttribute('x2',best.point.x);
        hover.horizontal.setAttribute('y1',best.point.y);hover.horizontal.setAttribute('y2',best.point.y);
        hover.dot.setAttribute('cx',best.point.x);hover.dot.setAttribute('cy',best.point.y);hover.dot.setAttribute('fill',best.group.color);
        for(const node of Object.values(hover))node.style.display='';
        let tip=svg.parentElement.querySelector('.chart-hover-tooltip');
        if(!tip){tip=document.createElement('div');tip.className='chart-hover-tooltip';svg.parentElement.append(tip);}
        tip.textContent=`${best.group.label}  ·  ${current.x.label}: ${best.point.rawX.toFixed(0)}  ·  ${current.y.label}: ${best.point.rawY.toPrecision(5)}`;
        tip.style.left=Math.max(8,Math.min(box.width-180,event.clientX-box.left+12))+'px';
        tip.style.top=Math.max(5,event.clientY-box.top-35)+'px';tip.hidden=false;
      });
      svg.addEventListener('pointerleave',()=>{if(svg._chartHover)for(const node of Object.values(svg._chartHover))node.style.display='none';const tip=svg.parentElement.querySelector('.chart-hover-tooltip');if(tip)tip.hidden=true;});
    }
    return model;
  }
  // Legend generated from the same definitions as the series, so colours can never drift from the lines.
  function legendItems(definitions) {
    return (definitions || []).map(d => ({ label: String(d.label || d.key), color: String(d.color || "#65d8e8") }));
  }
  function renderLegend(rootElement, definitions) {
    rootElement.replaceChildren();
    for (const item of legendItems(definitions)) {
      const dot = document.createElement("i"); dot.style.background = item.color;
      rootElement.append(dot, document.createTextNode(item.label));
    }
  }
  function renderBars(rootElement, values) {
    rootElement.replaceChildren();
    for (const item of values) {
      const row = document.createElement("div"); row.className = "metric-bar";
      const label = document.createElement("span"); label.textContent = item.label;
      const track = document.createElement("i");
      const fill = document.createElement("b");
      fill.style.width = Math.max(0, Math.min(100, Number(item.value) * 100)) + "%";
      track.append(fill);
      const value = document.createElement("strong");
      value.textContent = (Number(item.value || 0) * 100).toFixed(0) + "%";
      row.append(label, track, value); rootElement.append(row);
    }
  }
  const api = {
    formatExplanation: formatExplanation, series: series, latestRun: latestRun, actorUpdated: actorUpdated,
    actorUpdateNote: actorUpdateNote, MotionBuffer: MotionBuffer,
    createPoller: createPoller, chartModel: chartModel, renderChart: renderChart, renderBars: renderBars,
    legendItems: legendItems, renderLegend: renderLegend,
  };
  root.CobotDiagnosticsUI = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
