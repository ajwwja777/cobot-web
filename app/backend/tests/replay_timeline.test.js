"use strict";
const assert = require("assert").strict;
const { nodeTimes, snapTime, adjacentTime, create } = require("../segmented_frontend/replay_timeline.js");

const nodes = [
  { node_id: 1, frame_index: 30 },
  { node_id: 2, frame_index: 75 },
];

assert.deepEqual(nodeTimes(nodes, { sourceFps: 30, duration: 4 }), [
  { node: nodes[0], time: 1 },
  { node: nodes[1], time: 2.5 },
]);
assert.equal(snapTime(2.24, [1, 2.5], 0.35), 2.5);
assert.equal(snapTime(2.1, [1, 2.5], 0.35), 2.1);
assert.equal(adjacentTime(1.2, [1, 2.5], 1), 2.5);
assert.equal(adjacentTime(2.2, [1, 2.5], -1), 1);
assert.deepEqual(nodeTimes([], { sourceFps: 30, duration: 4 }), []);
assert.deepEqual(nodeTimes([{node_id:3,frame_index:50}], {
  sourceFrameCount: 100, duration: 8,
}), [{node:{node_id:3,frame_index:50},time:4}]);

const listeners = {};
const marker = { classList: { toggle(name, value) { this[name] = value; } } };
global.document = { createElement() { return {
  style: {}, classList: { toggle() {} }, setAttribute() {}, addEventListener() {},
}; } };
const markers = {
  children: { 0: marker, length: 1 },
  replaceChildren() { this.children = { length: 0 }; },
  append(value) { this.children = { 0: value, length: 1 }; },
};
const video = { duration: 1, currentTime: 0, addEventListener(name, fn) { listeners[name] = fn; } };
const slider = { value: "0", addEventListener(name, fn) { listeners["slider-" + name] = fn; } };
const label = { textContent: "" };
const timeline = create(video, { slider, markers, label });
timeline.setEpisode([{ node_id: 1, frame_index: 0 }], { source_fps: 30, duration: 1 });
assert.equal(markers.children.length, 1);
console.log("PASS timeline maps frames, snaps nearby values, jumps nodes, and handles fallback");
