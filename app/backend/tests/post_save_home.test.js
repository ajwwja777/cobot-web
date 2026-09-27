"use strict";
const assert = require("assert").strict;
const { choosePose, shortcutAction, run } = require("../segmented_frontend/post_save_home.js");

async function main() {
  assert.equal(choosePose(["origin", "plug", "plug2"], ""), "plug2");
  assert.equal(choosePose(["origin", "plug"], "origin"), "origin");
  assert.equal(choosePose(["camera"], "missing"), "camera");
  assert.equal(shortcutAction("ArrowRight", "idle", true), "start");
  assert.equal(shortcutAction("ArrowRight", "recording", true), "stop_home");
  assert.equal(shortcutAction("ArrowRight", "paused", false), "stop");
  assert.equal(shortcutAction("ArrowLeft", "recording", true), "discard");
  assert.equal(shortcutAction("ArrowLeft", "idle", true), null);
  assert.equal(shortcutAction("ArrowRight", "finalizing", true), null);

  let homeCalls = 0;
  await assert.rejects(run({
    target: "all", pose: "plug2", confirm: () => true,
    onPhase() {}, stop: async () => { throw new Error("save failed"); },
    startHome: async () => { homeCalls += 1; }, waitHome: async () => null,
  }), /save failed/);
  assert.equal(homeCalls, 0);

  const phases = [];
  const result = await run({
    target: "all", pose: "plug2", confirm: () => true,
    onPhase: phase => phases.push(phase),
    stop: async () => ({ capture_state: "committed" }),
    prepareNext: async () => {},
    startHome: async (target, pose) => ({ job_id: target + "-" + pose }),
    waitHome: async () => ({ phase: "completed" }),
  });
  assert.equal(result.job.job_id, "all-plug2");
  assert.deepEqual(phases, ["saving", "saved", "homing", "complete"]);
  console.log("PASS post-save home waits for a successful save and completes selected home");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
