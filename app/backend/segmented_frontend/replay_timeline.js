"use strict";

(function expose(root) {
  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  function nodeTimes(nodes, timing = {}) {
    const fps = finite(timing.sourceFps);
    const frames = finite(timing.sourceFrameCount);
    const duration = finite(timing.duration);
    return (Array.isArray(nodes) ? nodes : []).map(node => {
      const frame = finite(node.frame_index) || 0;
      let time = fps && fps > 0 ? frame / fps
        : frames && frames > 0 && duration != null ? frame / frames * duration
        : 0;
      if (duration != null) time = Math.min(Math.max(0, time), duration);
      return { node, time };
    });
  }
  function snapTime(value, times, threshold = 0.35) {
    const target = Number(value);
    let best = target, distance = Number(threshold);
    for (const item of times || []) {
      const gap = Math.abs(Number(item) - target);
      if (gap <= distance) { best = Number(item); distance = gap; }
    }
    return best;
  }
  function adjacentTime(value, times, direction) {
    const ordered = [...(times || [])].map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    if (direction > 0) {
      const next = ordered.find(time => time > Number(value) + 1e-6);
      return next === undefined ? Number(value) : next;
    }
    const previous = [...ordered].reverse().find(time => time < Number(value) - 1e-6);
    return previous === undefined ? Number(value) : previous;
  }
  function clock(seconds) {
    const value = Math.max(0, Number(seconds) || 0);
    const minutes = Math.floor(value / 60);
    return minutes + ":" + (value % 60).toFixed(1).padStart(4, "0");
  }
  function create(video, elements, callbacks = {}) {
    const slider = elements.slider, markers = elements.markers, label = elements.label;
    let nodes = [], timing = {}, mapped = [];

    function duration() {
      return Number.isFinite(video.duration) ? video.duration : Number(timing.duration) || 0;
    }
    function seek(value, allowSnap = true) {
      const times = mapped.map(item => item.time);
      const selected = allowSnap ? snapTime(Number(value), times) : Number(value);
      video.currentTime = Math.max(0, Math.min(duration() || selected, selected));
      slider.value = String(video.currentTime);
      renderTime();
      const hit = mapped.find(item => Math.abs(item.time - video.currentTime) < 1e-4);
      if (hit && typeof callbacks.onNode === "function") callbacks.onNode(hit.node);
    }
    function renderMarkers() {
      mapped = nodeTimes(nodes, {
        sourceFps: timing.source_fps,
        sourceFrameCount: timing.source_frame_count,
        duration: duration(),
      });
      markers.replaceChildren();
      for (const item of mapped) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "replay-marker";
        button.style.left = (duration() ? item.time / duration() * 100 : 0) + "%";
        button.title = "节点 " + item.node.node_id + " · " + clock(item.time);
        button.setAttribute("aria-label", button.title);
        button.addEventListener("click", () => seek(item.time, false));
        markers.append(button);
      }
    }
    function renderTime() {
      const current = Number(video.currentTime) || 0;
      slider.value = String(current);
      label.textContent = clock(current) + " / " + clock(duration());
      Array.from(markers.children || []).forEach((marker, index) => {
        marker.classList.toggle("active", Math.abs(mapped[index].time - current) <= 0.18);
      });
    }
    function setEpisode(nextNodes, nextTiming = {}) {
      nodes = Array.isArray(nextNodes) ? nextNodes : [];
      timing = nextTiming || {};
      slider.min = "0"; slider.max = String(duration()); slider.step = "0.01";
      renderMarkers(); renderTime();
    }
    slider.addEventListener("input", () => seek(slider.value, true));
    slider.addEventListener("keydown", event => {
      if (!event.shiftKey || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      seek(adjacentTime(video.currentTime, mapped.map(item => item.time),
        event.key === "ArrowRight" ? 1 : -1), false);
    });
    video.addEventListener("loadedmetadata", () => setEpisode(nodes, timing));
    video.addEventListener("timeupdate", renderTime);
    return { setEpisode, seek };
  }

  const api = { nodeTimes, snapTime, adjacentTime, create };
  root.CobotReplayTimeline = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
