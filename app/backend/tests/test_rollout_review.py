import json
from pathlib import Path
import sqlite3

from cobot_console.rollout_review import alignment, catalog_runs, read_run


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_alignment_preserves_delayed_frames_and_uses_monotonic():
    video = dict(source_fps=2, source_frame_count=4, frame_timestamps=[100, 100.5, 102, 102.5])
    rollout = dict(points=[dict(monotonic_seconds=100, seconds=1, timestamp=9999),
                          dict(monotonic_seconds=102.5, seconds=3.5, timestamp=10001.5)])
    result = alignment(video, rollout)
    assert result["status"] == "aligned"
    assert result["segments"][1] == dict(video_start=.5, video_end=1, episode_start=1.5, episode_end=3)
    # wall clocks alone are explicitly insufficient, even when they appear plausible.
    for point in rollout["points"]:
        del point["monotonic_seconds"]
    assert alignment(video, rollout)["status"] == "unavailable"


def test_alignment_does_not_extrapolate_or_accept_corrupt_clocks():
    video = dict(source_fps=2, source_frame_count=4, frame_timestamps=[100, 100.5, 101, 101.5])
    rollout = dict(points=[dict(monotonic_seconds=100.25, seconds=1),
                          dict(monotonic_seconds=101.25, seconds=2)])
    result = alignment(video, rollout)
    assert result["segments"][0]["video_start"] == .25
    assert result["segments"][-1]["video_end"] == 1.25
    video["frame_timestamps"][2] = 99
    assert alignment(video, rollout)["status"] == "unavailable"


def fixture(tmp_path):
    run, data = tmp_path / "run", tmp_path / "data"
    ep = dict(episode_uuid="one", count=10, outcome="success", started_at=100,
              finished_at=112, actor_version=7, training_eligible=True)
    write(run / "episodes/one/finished.json", ep)
    write(run / "telemetry/rollout.json", dict(episode_uuid="one", actor_version=7,
          points=[dict(seconds=1, timestamp=101, q=.2), dict(seconds=2, timestamp=102, q=.3)]))
    side = data / ".segments/one"
    write(side / "sidecar.json", dict(episode_uuid="one", model_id="model", capture_state="committed",
                                     has_hil=True, episode_outcome="success"))
    write(side / "review-video.json", dict(episode_uuid="one", state="ready", source_fps=2,
                                         source_frame_count=3, frame_timestamps=[100, 100.5, 101]))
    (side / "review.mp4").write_bytes(b"fixture")
    return dict(model_id="model", options=dict(rtc_delay=5, edit_scale=.01),
                run_dir=str(run), data_root=str(data))


def test_read_only_history_video_hil_identity_and_missing_values(tmp_path):
    spec = fixture(tmp_path)
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    result = read_run(spec)
    review = result["episodes"][0]["review"]
    assert review["video_url"].startswith("/api/segmented-teach/episodes/one/preview.mp4?")
    assert review["alignment"]["status"] == "unavailable"
    assert review["duration_seconds"] == 12 and review["hil"] is True
    assert result["success"]["episodes"][0]["episode"] == 1
    assert result["success"]["episodes"][0]["outcome"] == "success"
    assert result["episodes"][0]["review"]["actor_version"] == 7
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.rglob("*") if p.is_file()}
    # Same UUID in another model must not expose this recording.
    changed = read_run(dict(spec, model_id="another"))
    assert changed["episodes"][0]["review"]["video_url"] is None
    assert changed["episodes"][0]["review"]["hil"] is None


def test_symlink_video_rejected_and_unknown_metadata_null(tmp_path):
    spec = fixture(tmp_path)
    video = Path(spec["data_root"]) / ".segments/one/review.mp4"
    video.unlink()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    video.symlink_to(outside)
    result = read_run(spec)
    assert result["episodes"][0]["review"]["video_url"] is None
    write(Path(spec["run_dir"]) / "telemetry/rollout.json", dict(episode_uuid="new", points=[dict(seconds=1, q=.1)]))
    entry = next(p for p in read_run(spec)["episodes"] if p["episode_uuid"] == "new")
    assert entry["review"]["duration_seconds"] is None and entry["review"]["outcome"] is None


def test_readonly_archive_merges_new_fields_without_mutating_database(tmp_path):
    spec = fixture(tmp_path)
    path = Path(spec["run_dir"]) / "telemetry/rollout-history.sqlite3"
    with sqlite3.connect(str(path)) as db:
        db.execute("CREATE TABLE episodes(id TEXT, started REAL, payload TEXT)")
        db.execute("INSERT INTO episodes VALUES (?,?,?)", ("one", 100, json.dumps(
            dict(episode_uuid="one", points=[dict(seconds=.5, timestamp=100.5, q=.1)]))))
    before = path.read_bytes()
    result = read_run(spec)
    assert len(result["episodes"]) == 1
    assert path.read_bytes() == before


def test_options_and_catalogue_require_registered_identity(tmp_path):
    spec = fixture(tmp_path)
    Path(spec["run_dir"] + "--d3-edit005").mkdir()
    model = dict(id="model", runtime_adapter="expo_online", online_run_dir=spec["run_dir"],
                 data_directories=dict(collection=spec["data_root"]),
                 expo_online_options=dict(defaults=spec["options"]))
    result = catalog_runs(dict(models=[model]))
    assert len(result) == 2
    assert result[1]["options"] == dict(rtc_delay=3, edit_scale=.05)
    del model["expo_online_options"]
    unknown = catalog_runs(dict(models=[model]))
    assert unknown[0]["options"] is None


def test_hil_cache_must_match_finished_revision(tmp_path):
    spec = fixture(tmp_path)
    result = read_run(spec)
    finished = Path(spec["run_dir"]) / "episodes/one/finished.json"
    cache = tmp_path / "cache"
    path = cache / (result["run_key"] + ".json")
    write(path, dict(episodes=dict(one=dict(stamp=finished.stat().st_mtime_ns,
                                          summary=dict(hil_steps=0)))))
    current = read_run(spec, cache)["episodes"][0]["review"]
    assert current["hil"] is False and current["hil_source"] == "journal_valid_rows"
    write(path, dict(episodes=dict(one=dict(stamp=0, summary=dict(hil_steps=0)))))
    stale = read_run(spec, cache)["episodes"][0]["review"]
    assert stale["hil"] is True and stale["hil_source"] == "recorder_sidecar"


def test_alignment_rejects_wall_clock_jump_backwards_and_nonoverlap():
    video = dict(source_fps=2, source_frame_count=2, frame_timestamps=[100, 101])
    points = [dict(monotonic_seconds=100, seconds=3), dict(monotonic_seconds=101, seconds=2)]
    assert alignment(video, dict(points=points))["status"] == "unavailable"
    points[1]["seconds"] = 4
    video["frame_timestamps"] = [200, 201]
    assert alignment(video, dict(points=points))["status"] == "unavailable"
