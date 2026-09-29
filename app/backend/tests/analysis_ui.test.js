
const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),{JSDOM}=require("jsdom");
test("analysis preserves legacy IDs, uses GET only, retains last results on failure and supports language changes",async()=>{
 const dom=new JSDOM('<html lang="en"><body><section data-page="training" class="page active"><article><select id="training-run"></select></article><div id="training-figures"></div><span id="training-live-state"></span></section><section class="page" data-page="analysis"><div id="analysis-root"></div></section></body></html>',{runScripts:"outside-only",url:"http://test/"});
 const w=dom.window;w.setInterval=()=>0;const requests=[];let fail=false;
 w.CobotDiagnosticsUI={renderChart:(svg,rows,defs)=>{svg.dataset.rows=rows.length;svg.dataset.keys=defs.map(x=>x.key).join(",");},renderLegend:()=>{}};
 w.fetch=async(url,options)=>{requests.push({url,options});if(fail)return {ok:false,status:503};return {ok:true,json:async()=>({
  status:{global_step:100,actor_version:50,replay_size:200,pending_update_budget:0,timestamp:1},stale:true,generated_at:2,
  publication:{published_learner_step:80,published_actor_version:40},runs:[{id:0,start:1,end:100}],selected_run:0,
  series:[{global_step:1,critic_loss:.1}],actor_series:[{global_step:2,actor_loss:-.5}],recent_count:100,
  episode_groups:[{cohort:"online / stochastic",version:"40",count:2,autonomous_successes:1,assisted_successes:1,failures:0,hil_count:1,autonomous_rate:.5,autonomous_ci:[.1,.9]}],
  episodes:[{key:"1:2",episode_id:2,cohort:"online / stochastic",success:1,version:"40",timestamp:1}],
  sources:[],config:{},projection:{},excluded_uncommitted:3
 })};};
 w.eval(fs.readFileSync(path.join(__dirname,"../segmented_frontend/analysis_ui.js"),"utf8"));
 w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
 await new Promise(resolve=>setTimeout(resolve,30));
 assert.equal(w.document.querySelectorAll("#training-stats .analysis-stat").length,4);
 assert.equal(w.document.querySelectorAll("#training-live-state").length,1);
 assert(w.document.querySelector("#training-live-state").closest(".analysis-compatibility"));
 assert.match(w.document.querySelector("#training-analysis-status").textContent,/Historical snapshot/);
 assert.equal(w.document.querySelector("#analysis-actor").dataset.rows,"1");
 assert.match(w.document.querySelector("#analysis-outcome-table").textContent,/50.0%/);
 w.document.documentElement.lang="zh";w.document.dispatchEvent(new w.Event("cobot:language"));
 assert.match(w.document.querySelector("#training-analysis-status").textContent,/历史快照/);
 fail=true;await w.CobotAnalysisUI.refresh();
 assert.match(w.document.querySelector("#analysis-status").textContent,/503/);
 assert.equal(w.document.querySelectorAll("#training-stats .analysis-stat").length,4);
 assert(requests.every(x=>x.url.startsWith("/api/analysis/rlt?run=")&&!x.options.method));
 dom.window.close();
});
