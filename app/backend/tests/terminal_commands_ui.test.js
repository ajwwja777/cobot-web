const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const {JSDOM} = require("jsdom");

test("output defaults to a usable CAN terminal recipe and preserves raw process details", async () => {
  const dom = new JSDOM(fs.readFileSync("segmented_frontend/index.html", "utf8"),
    {url:"http://localhost", runScripts:"outside-only", pretendToBeVisual:true});
  const w = dom.window;
  // These containers are normally mounted by workspace_ui before dock_ui.
  w.document.querySelector("main").insertAdjacentHTML("beforeend",
    '<section id="camera-dock"><button id="camera-pin"></button></section><section id="log-dock"></section>');
  w.document.querySelector(".sidebar").insertAdjacentHTML("beforeend",
    '<button id="camera-nav"></button><button class="sidebar-toggle"></button>');
  w.CobotWorkspaceUI = {};
  w.setInterval = () => 0;
  w.ResizeObserver = class { observe() {} disconnect() {} };
  w.CobotPreferences = {language:"zh", text: value => value};
  const terminal = "cd /home/agilex/jiaan/project/cobot-control\n./scripts/can_up.sh";
  const job = {id:"can-1",component:"can",phase:"completed",pid:23456,
    command_text:"/home/agilex/jiaan/project/cobot-web/scripts/can_web.sh configure",
    process_command:"",cwd:"/home/agilex/jiaan/project/cobot-web",
    cwd_command:"cd /home/agilex/jiaan/project/cobot-web",terminal_command:terminal,
    implementation:{zh:"实际实现：can_config_cobot.sh task2",en:"Implementation: can_config_cobot.sh task2"},
    log_path:"/site/can.log",log_tail:"CAN ready",can_stop:false};
  w.fetch = async () => ({ok:true,json:async()=>({tasks:[job],
    commands:[{label:"配置五臂 CAN",group:"CAN",command:terminal,implementation:job.implementation}],
    updated_at:1})});
  try {
    w.eval(fs.readFileSync("segmented_frontend/dock_ui.js","utf8"));
    w.document.dispatchEvent(new w.Event("DOMContentLoaded"));
    await w.CobotOutputPanel.refresh();
    await new Promise(resolve=>setTimeout(resolve,0));
    const text = w.document.querySelector("#output-command-text");
    const select = w.document.querySelector("#common-command-select");
    assert(text.textContent.startsWith(terminal));
    assert(text.textContent.includes("# 实际实现：can_config_cobot.sh task2"));
    assert(!text.textContent.includes("can_web.sh"));
    assert(!text.textContent.includes("read -rsp"));
    assert(w.document.querySelector("#output-task-meta").textContent.includes("23456"));
    select.value = "process";
    select.dispatchEvent(new w.Event("change"));
    assert(text.textContent.includes(job.command_text));
    assert(!text.textContent.includes("./scripts/can_up.sh"));
    select.value = "current";
    select.dispatchEvent(new w.Event("change"));
    w.CobotPreferences.language = "en";
    w.document.dispatchEvent(new w.Event("cobot:language"));
    assert(text.textContent.includes("# Implementation:"));
    assert(!text.textContent.includes("实际实现"));
    select.value = "0";
    select.dispatchEvent(new w.Event("change"));
    assert(text.textContent.startsWith(terminal));
  } finally { dom.window.close(); }
});
