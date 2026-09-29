const test=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),{JSDOM}=require("jsdom");
test("audit distinguishes terminal labels, missing historical batches and training-seen evaluation",()=>{
 const dom=new JSDOM('<html lang="en"><div id="grid"></div></html>',{runScripts:"outside-only"}),w=dom.window;
 w.eval(fs.readFileSync("segmented_frontend/replay_audit_ui.js","utf8"));
 w.CobotReplayAuditUI.mount(w.document.querySelector("#grid"));
 const d={replay_composition:{generated_at:1,transitions:{count:10,dimensions:{outcome:[{label:"success",count:7,ratio:.7}]}}},learning_diagnosis:{versions:[{learner_step:500,actor_version:250}]}};
 w.CobotReplayAuditUI.render(d);
 assert.match(w.document.querySelector("#audit-table").textContent,/70.0%/);
 assert.match(w.document.querySelector("#audit-validation-note").textContent,/NOT independent/);
 const scope=w.document.querySelector("#audit-scope");scope.value="batch";scope.dispatchEvent(new w.Event("change"));
 assert.match(w.document.querySelector("#audit-warning").textContent,/not recorded/);
 assert.match(w.document.querySelector("#audit-sensitivity-note").textContent,/NOT attention/);
 dom.window.close();
});
