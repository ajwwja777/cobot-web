const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {JSDOM}=require("jsdom");

test("credit analysis renders registered experiments and all seven action dimensions",()=>{
 const dom=new JSDOM('<html lang="en"><body><div id="grid"></div></body></html>',{runScripts:"outside-only"});
 const w=dom.window,charts=[];
 w.CobotDiagnosticsUI={renderChart:(svg,rows,series)=>charts.push({svg,rows,series})};
 w.eval(fs.readFileSync(path.join(__dirname,"../segmented_frontend/replay_audit_ui.js"),"utf8"));
 w.CobotReplayAuditUI.mount(w.document.getElementById("grid"));
 const actions=[[0,1,2,3,4,5,.01],[.1,1,2,3,4,5,.02]];
 const validation={human_mae_rad:.002,reference_human_mae_rad:.003,hil_steps_improved_ratio:.6,
  correction_human_cosine:.4,mc_rmse:.12,q_by_portion:{},
  traces:[{episode:7,step:10,outcome:"success",human_mask:[true,true],reference:actions,actor:actions,executed:actions}]};
 w.CobotReplayAuditUI.render({credit_assignment:{updates:2000,finished_at:1,baseline:validation,
  experiments:[{variant:"mc_30",seed:42,validation}]}});
 assert.equal(w.document.querySelectorAll("#audit-credit-charts svg").length,7);
 assert.match(w.document.querySelector("#audit-credit-note").textContent,/not an independent test set/);
 const picker=w.document.getElementById("audit-credit-variant");
 picker.value="mc_30";picker.dispatchEvent(new w.Event("change"));
 assert.equal(picker.value,"mc_30");
 assert.equal(charts.at(-1).series.length,3);
 assert.equal(charts.at(-1).rows[0].reference,.01);
 w.document.documentElement.lang="zh";
 w.CobotReplayAuditUI.render({credit_assignment:{baseline:validation,experiments:[]}});
 assert.match(w.document.querySelector("#audit-credit h3").textContent,/\u5956\u52b1/);
 assert.equal(w.document.querySelectorAll("#audit-credit-charts svg").length,7);
 dom.window.close();
});
