"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  formatApiError,
  outcomeRequest,
  deleteRequest,
  episodeOutcomeText,
  previewProgress,
  phaseText,
  seriesRequest,
  stopProgress,
} = require("../frontend/workflow.js");

test("structured FastAPI errors become readable text", () => {
  const detail = [
    { loc: ["body", "failure_stage"], msg: "Field required" },
    { loc: ["body", "failure_type"], msg: "Field required" },
  ];

  const result = formatApiError(detail);

  assert.match(result, /failure_stage/);
  assert.match(result, /failure_type/);
  assert.doesNotMatch(result, /\[object Object\]/);
});

test("known recorder errors have operator-readable Chinese messages", () => {
  assert.equal(
    formatApiError("latest_episode_labels_required"),
    "请先为上一条 episode 选择成功或失败。",
  );
  assert.equal(
    formatApiError("recorder_not_ready"),
    "记录器尚未就绪，请确认 Task2、ROS 和三路相机均已启动。",
  );
});

test("outcome decisions map to the minimal API contract", () => {
  assert.deepEqual(outcomeRequest("episode-uuid", "success"), {
    episode_uuid: "episode-uuid",
    outcome: "success",
  });
  assert.deepEqual(outcomeRequest("episode-uuid", "failure"), {
    episode_uuid: "episode-uuid",
    outcome: "failure",
  });
  assert.throws(() => outcomeRequest("episode-uuid", "aborted"));
});

test("permanent deletion repeats UUID and preview progress is readable", () => {
  assert.deepEqual(deleteRequest("episode-uuid"), {
    episode_uuid: "episode-uuid",
  });
  assert.equal(
    previewProgress({ state: "generating", completed_frames: 30, total_frames: 90 }),
    "正在生成回放：30 / 90",
  );
  assert.equal(previewProgress({ state: "ready" }), "回放已就绪");
  assert.equal(
    previewProgress({ state: "error", error_code: "preview_generation_failed" }),
    "回放生成失败，请查看服务日志",
  );
});

test("recording stop progress distinguishes saving from committed", () => {
  assert.deepEqual(
    stopProgress({ state: "stopping", frames_written: 1236 }),
    {
      complete: false,
      failed: false,
      message: "采样已停止，正在原子保存 1236 frames…",
    },
  );
  assert.deepEqual(
    stopProgress({
      state: "stopped",
      episode_file: "episode_000011.hdf5",
      frames_written: 1236,
    }),
    {
      complete: true,
      failed: false,
      message: "episode_000011.hdf5 已完整保存，共 1236 frames。",
    },
  );
  assert.equal(stopProgress({ state: "error" }).failed, true);
});

test("episode history names the actual outcome instead of generic labeled", () => {
  assert.equal(episodeOutcomeText({ episode_outcome: "success" }), "成功");
  assert.equal(episodeOutcomeText({ episode_outcome: "failure" }), "失败");
  assert.equal(episodeOutcomeText({ episode_outcome: "unknown" }), "待标注");
  assert.equal(episodeOutcomeText({}), "待标注");
});

test("global intervention phase text distinguishes one arm and both arms", () => {
  assert.equal(
    phaseText({ start_frame: 623, end_frame: 623, human_sides: ["left"] }),
    "frame 623：左臂人工接管",
  );
  assert.equal(
    phaseText({
      start_frame: 624,
      end_frame: 880,
      human_sides: ["left", "right"],
    }),
    "frame 624–880：双臂人工接管",
  );
});

test("series request trims persistent experiment fields and omits UI-only state", () => {
  assert.deepEqual(
    seriesRequest({
      data_root: " /data/rollouts ",
      task_id: " in_the_pot ",
      model_id: " pi05 ",
      checkpoint_id: " step_2000 ",
      dataset_round: " round_001 ",
      episode_index: "99",
      max_timesteps: "3600",
    }),
    {
      data_root: "/data/rollouts",
      task_id: "in_the_pot",
      model_id: "pi05",
      checkpoint_id: "step_2000",
      dataset_round: "round_001",
    },
  );
});
