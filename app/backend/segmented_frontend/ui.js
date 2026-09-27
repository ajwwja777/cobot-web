"use strict";

(function expose(root) {
  function buttonsFor(state) {
    return {
      pause: state === "recording",
      resume: state === "paused",
      marker: state === "recording",
      stop: state === "recording" || state === "paused",
      discard: state === "recording" || state === "paused",
    };
  }

  function versionRequest(status) {
    return {
      episode_uuid: String(status.episode_uuid),
      generation: Number(status.generation),
    };
  }

  function dataRootQuery(dataRoot) {
    return new URLSearchParams({ data_root: String(dataRoot).trim() }).toString();
  }

  function nodeLabel(node, fps = 30) {
    const seconds = Number(node.frame_index || 0) / Number(fps || 30);
    const events = Array.isArray(node.merged_events) ? node.merged_events : [];
    const hasPause = events.some((event) => event === "ui_pause" || event.startsWith("teach_exit:"));
    const hasResume = events.some((event) => event === "ui_resume" || event.startsWith("teach_enter:"));
    const state = hasPause && hasResume
      ? "暂停/继续边界"
      : node.capture_state === "recording" ? "录制" : node.capture_state === "paused" ? "暂停" : node.kind;
    const sides = Array.isArray(node.merged_sides) && node.merged_sides.length
      ? ` · ${node.merged_sides.join("+")}`
      : "";
    return `节点 ${node.node_id} · ${seconds.toFixed(2)}s · ${state} · ${node.primary_trigger}${sides}`;
  }

  function intervalsFromNodes(nodes) {
    const result = [];
    for (let index = 0; index + 1 < nodes.length; index += 1) {
      const before = nodes[index];
      const after = nodes[index + 1];
      const start = Number(before.frame_index || 0);
      const end = Number(after.frame_index || 0);
      if (before.capture_state !== "recording" || end <= start) continue;
      result.push({
        interval_id: result.length + 1,
        start_node_id: Number(before.node_id),
        end_node_id: Number(after.node_id),
        start_frame: start,
        end_frame_exclusive: end,
      });
    }
    return result;
  }

  const api = { buttonsFor, dataRootQuery, intervalsFromNodes, nodeLabel, versionRequest };
  root.SegmentedTeachUI = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
