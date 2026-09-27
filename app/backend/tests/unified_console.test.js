"use strict";

const assert = require("assert").strict;
const {
  parseApiResponse, rltButtons, rltGuidance, rltShortcutAction,
  releaseOptionLabel, releaseSwitchAllowed,
} = require("../segmented_frontend/console_ui.js");
const test = (name, fn) => Promise.resolve().then(fn).then(
  () => console.log("PASS " + name),
  (error) => { console.error("FAIL " + name, error); process.exitCode = 1; },
);
function response(status, contentType, text) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => name.toLowerCase() === "content-type" ? contentType : null },
    text: async () => text,
  };
}
test("HTML backend failure becomes a readable API error", async () => {
  await assert.rejects(
    parseApiResponse(response(502, "text/html", "<!DOCTYPE html><h1>Bad gateway</h1>")),
    /HTTP 502.*text\/html.*JSON/,
  );
});
test("JSON detail is preserved for failed requests", async () => {
  await assert.rejects(
    parseApiResponse(response(409, "application/json", '{"detail":"rlt_mode_not_selected"}')),
    /rlt_mode_not_selected/,
  );
});
test("successful JSON response is parsed", async () => {
  assert.deepEqual(
    await parseApiResponse(response(200, "application/json; charset=utf-8", '{"phase":"running"}')),
    { phase: "running" },
  );
});
test("RLT buttons fail closed while backend is offline", async () => {
  const buttons = rltButtons({ selected_mode: "rlt", rlt_backend_phase: "offline" }, null);
  assert.equal(Object.values(buttons).some(Boolean), false);
});
test("RLT exposes terminal outcomes during rollout and after pause, but not HIL", async () => {
  const consoleStatus = { selected_mode: "rlt", rlt_backend_phase: "running" };
  const live = rltButtons(consoleStatus, { phase: "rollout", policy_paused: false });
  assert.equal(live.pause, true);
  assert.equal(live.resume, false);
  assert.equal(live.success, true);
  assert.equal(rltButtons(consoleStatus,{phase:"hil",policy_paused:true}).success,false);
  const paused = rltButtons(consoleStatus, { phase: "paused", policy_paused: true });
  assert.equal(paused.pause, false);
  assert.equal(paused.resume, true);
  assert.equal(paused.success, true);
  assert.equal(paused.failure, true);
  assert.equal(paused.abort, true);
});
test("RLT arrow shortcuts follow the enabled rollout action", () => {
  assert.equal(rltShortcutAction({ start: true }, "ArrowRight"), "start");
  assert.equal(rltShortcutAction({ pause: true }, "ArrowRight"), "pause");
  assert.equal(rltShortcutAction({ resume: true }, "ArrowRight"), "resume");
  assert.equal(rltShortcutAction({ next: true }, "ArrowRight"), "next");
  assert.equal(rltShortcutAction({ success: true }, "ArrowUp"), "success");
  assert.equal(rltShortcutAction({ failure: true }, "ArrowDown"), "failure");
  assert.equal(rltShortcutAction({ abort: true }, "ArrowLeft"), "abort");
  assert.equal(rltShortcutAction({ pause: false }, "ArrowRight"), null);
});
test("guidance explains loading and faults", async () => {
  assert.ok(/加载/.test(rltGuidance({ rlt_backend_phase: "loading" }, null)));
  assert.ok(/learner_exit/.test(
    rltGuidance({ rlt_backend_phase: "fault", rlt_lifecycle: { error_code: "learner_exit" } }, null),
  ));
});
test("release picker labels and switch guard", () => {
  assert.equal(releaseOptionLabel({ name: "rtc-round-2.json", kind: "只模仿（不用 critic）", actor_updates: 5092, current: true }),
    "● 只模仿（不用 critic） · actor 5092 · rtc-round-2");
  const listing = { current: "a.json", switch_phases: ["disarmed", "waiting_scene"] };
  const rlt = { selected_mode: "rlt" };
  assert.equal(releaseSwitchAllowed(rlt, { phase: "disarmed" }, listing, "b.json", false), true);
  assert.equal(releaseSwitchAllowed(rlt, { phase: "rollout" }, listing, "b.json", false), false);
  assert.equal(releaseSwitchAllowed(rlt, { phase: "disarmed" }, listing, "a.json", false), false);
  assert.equal(releaseSwitchAllowed(rlt, { phase: "disarmed" }, listing, "b.json", true), false);
  assert.equal(releaseSwitchAllowed({ selected_mode: "normal" }, { phase: "disarmed" }, listing, "b.json", false), false);
  assert.equal(releaseSwitchAllowed(rlt, null, { current: "a.json", switch_phases: ["offline"] }, "b.json", false), true);
});
