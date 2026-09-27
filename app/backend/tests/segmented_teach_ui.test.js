"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buttonsFor,
  dataRootQuery,
  intervalsFromNodes,
  nodeLabel,
  versionRequest,
} = require("../segmented_frontend/ui.js");

test("trainable node intervals exclude paused gaps and split markers", () => {
  assert.deepEqual(intervalsFromNodes([
    { node_id: 1, capture_state: "recording", frame_index: 0 },
    { node_id: 2, capture_state: "recording", frame_index: 3 },
    {
      node_id: 3,
      capture_state: "recording",
      frame_index: 5,
      merged_events: ["teach_exit:left", "teach_enter:left"],
    },
    { node_id: 4, capture_state: "committed", frame_index: 8 },
  ]), [
    { interval_id: 1, start_node_id: 1, end_node_id: 2, start_frame: 0, end_frame_exclusive: 3 },
    { interval_id: 2, start_node_id: 2, end_node_id: 3, start_frame: 3, end_frame_exclusive: 5 },
    { interval_id: 3, start_node_id: 3, end_node_id: 4, start_frame: 5, end_frame_exclusive: 8 },
  ]);
});

test("selected data root is safely encoded in history requests", () => {
  assert.equal(
    dataRootQuery("/media/agilex/Getea1/jiaan/data/cobot realworld/raw"),
    "data_root=%2Fmedia%2Fagilex%2FGetea1%2Fjiaan%2Fdata%2Fcobot+realworld%2Fraw",
  );
});

test("button availability follows capture state", () => {
  assert.deepEqual(buttonsFor("recording"), {
    pause: true,
    resume: false,
    marker: true,
    stop: true,
    discard: true,
  });
  assert.deepEqual(buttonsFor("paused"), {
    pause: false,
    resume: true,
    marker: false,
    stop: true,
    discard: true,
  });
  assert.deepEqual(buttonsFor("idle"), {
    pause: false,
    resume: false,
    marker: false,
    stop: false,
    discard: false,
  });
});

test("generation request preserves active episode identity", () => {
  assert.deepEqual(
    versionRequest({ episode_uuid: "ep-1", generation: 7 }),
    { episode_uuid: "ep-1", generation: 7 },
  );
});

test("node label exposes interval boundary and merged triggers", () => {
  assert.equal(
    nodeLabel({
      node_id: 2,
      kind: "transition",
      capture_state: "paused",
      frame_index: 150,
      primary_trigger: "teach_exit",
      merged_sides: ["right", "left"],
    }, 30),
    "节点 2 · 5.00s · 暂停 · teach_exit · right+left",
  );
});

test("merged pause resume node is shown as one boundary", () => {
  assert.equal(
    nodeLabel({
      node_id: 3,
      kind: "transition",
      capture_state: "recording",
      frame_index: 210,
      primary_trigger: "teach_exit",
      merged_events: ["teach_exit:left", "ui_resume"],
      merged_sides: ["left"],
    }, 30),
    "节点 3 · 7.00s · 暂停/继续边界 · teach_exit · left",
  );
});
