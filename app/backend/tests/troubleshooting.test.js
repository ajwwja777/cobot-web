const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const {JSDOM}=require("jsdom");
const moduleApi=require("../segmented_frontend/troubleshooting.js");

test("mutation errors remain uncertain and storage failures give specific advice",()=>{
  assert.equal(moduleApi.guidance(503,"POST","/api/rlt/episode/failure").uncertain,true);
  assert.equal(moduleApi.guidance(503,"GET","/api/rlt/session").uncertain,false);
  assert.match(moduleApi.guidance(500,"POST","/api/start","Input/output error").en,/Storage I\/O/);
  assert.match(moduleApi.guidance(422,"POST","/api/start").en,/parameters/);
});

test("diagnostics redact credentials recursively",()=>{
  assert.deepEqual(moduleApi.redact({input:{sudo_password:"secret",confirmation_token:"t"},phase:"idle"}),
    {input:{sudo_password:"[redacted]",confirmation_token:"[redacted]"},phase:"idle"});
});

test("failed request is captured once, unchanged, with safe visible output",async()=>{
  const dom=new JSDOM('<div class="task-output-toolbar"></div>',{url:"http://localhost",runScripts:"outside-only"});
  const w=dom.window;
  let calls=0;
  w.fetch=async()=>{calls++;return new Response(JSON.stringify({detail:"<img src=x>",input:{sudo_password:"secret"}}),
    {status:503,headers:{"Content-Type":"application/json"}});};
  w.CobotPreferences={language:"en"};
  w.eval(fs.readFileSync("segmented_frontend/troubleshooting.js","utf8"));
  w.CobotTroubleshooting.mount();
  const response=await w.fetch("/api/rlt/episode/failure",{method:"POST",body:"{}"});
  assert.equal(response.status,503);
  assert.equal(calls,1);
  assert.equal((await response.json()).input.sudo_password,"secret");
  const failures=w.CobotTroubleshooting.failures;
  assert.equal(failures.length,1);
  assert.ok(!failures[0].detail.includes("secret"));
  const dialog=w.document.querySelector("dialog");
  dialog.setAttribute("open","");
  w.CobotTroubleshooting.record(failures[0]);
  assert.equal(dialog.querySelectorAll("img").length,0);
  assert.match(dialog.textContent,/unconfirmed/);
  assert.match(w.document.querySelector("#console-recovery-open").textContent,/Diagnostics/);
  dom.window.close();
});
