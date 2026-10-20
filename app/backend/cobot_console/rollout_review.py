"""Read-only rollout/video metadata. No model imports, encoding or control calls."""
import bisect
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlencode


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def alignment(video, rollout):
    """Use the recorder and Actor's same-host monotonic clocks, never wall time.

    MP4 PTS is frame_index/source_fps because the recorder encodes raw video at
    fixed frame rate. Recorded frame timestamps preserve pauses/dropped frames.
    Map only the overlap covered by paired Q monotonic/episode-time samples;
    old wall-clock-only telemetry cannot be aligned retrospectively.
    """
    unavailable = dict(status="unavailable", basis="No paired monotonic Q timestamps", segments=[])
    fps, frames = video.get("source_fps"), video.get("frame_timestamps")
    if not finite(fps) or fps <= 0 or not isinstance(frames, list) or len(frames) < 2:
        return dict(unavailable, basis="Review video has no valid frame timestamp mapping")
    if len(frames) != video.get("source_frame_count") or not all(finite(t) for t in frames):
        return dict(unavailable, basis="Review video frame metadata is inconsistent")
    if any(b <= a for a, b in zip(frames, frames[1:])):
        return dict(unavailable, basis="Review video monotonic timestamps are not increasing")
    points = [p for p in rollout.get("points", []) if
              finite(p.get("monotonic_seconds")) and finite(p.get("seconds"))]
    if len(points) < 2:
        return unavailable
    points.sort(key=lambda p: p["monotonic_seconds"])
    mono = [p["monotonic_seconds"] for p in points]
    seconds = [p["seconds"] for p in points]
    if any(b <= a for a, b in zip(mono, mono[1:])) or any(b <= a for a, b in zip(seconds, seconds[1:])):
        return dict(unavailable, basis="Q timestamp clocks are not increasing")

    def episode_time(stamp):
        i = max(0, min(len(mono) - 2, bisect.bisect_right(mono, stamp) - 1))
        return seconds[i] + (stamp - mono[i]) / (mono[i + 1] - mono[i]) * (seconds[i + 1] - seconds[i])

    segments = []
    for i, (left, right) in enumerate(zip(frames, frames[1:])):
        start, end = max(left, mono[0]), min(right, mono[-1])
        if end <= start:
            continue
        segments.append(dict(
            video_start=(i + (start - left) / (right - left)) / fps,
            video_end=(i + (end - left) / (right - left)) / fps,
            episode_start=episode_time(start), episode_end=episode_time(end)))
    if not segments:
        return dict(unavailable, basis="Video and Q monotonic clock ranges do not overlap")
    return dict(status="aligned", basis="Recorded video frame PTS + same-Cobot monotonic Q timestamps",
                segments=segments)


def read_rollouts(root, limit=300, strict=False):
    """Read SQLite in mode=ro; never invoke the archive's create/write helpers."""
    result = {}
    path = Path(root) / "telemetry/rollout-history.sqlite3"
    if path.is_file():
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                rows = db.execute("SELECT id,payload FROM episodes ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
            for uid, payload in rows:
                value = json.loads(payload)
                if value.get("episode_uuid") == uid:
                    result[uid] = value
        except (sqlite3.Error, ValueError, OSError):
            if strict:
                raise
    current = read_json(Path(root) / "telemetry/rollout.json")
    uid = current.get("episode_uuid")
    if isinstance(uid, str) and uid:
        old = result.get(uid, {})
        points = {(p.get("timestamp"), p.get("seconds")): p for p in old.get("points", [])}
        points.update({(p.get("timestamp"), p.get("seconds")): p for p in current.get("points", [])})
        result[uid] = dict(old, **current)
        result[uid]["points"] = sorted(points.values(), key=lambda p: p.get("seconds", 0))
    return result


def find_sidecars(data_root):
    root = Path(data_root) / ".segments"
    result = {}
    for pattern in ("*/sidecar.json", "*/*/*/*/sidecar.json"):
        for path in root.glob(pattern):
            if path.is_symlink():
                continue
            try:
                path.resolve().relative_to(root.resolve())
            except (ValueError, OSError):
                continue
            uid = path.parent.name
            # Ambiguous UUIDs must not select an arbitrary video.
            result[uid] = None if uid in result else path
    return result


def review_for(uid, rollout, finished, sidecar_path, spec, cached_summary=None):
    sidecar = read_json(sidecar_path) if sidecar_path else {}
    if sidecar.get("episode_uuid") != uid or sidecar.get("model_id") != spec["model_id"]:
        sidecar = {}
    video_path = sidecar_path.parent / "review.mp4" if sidecar else None
    video = read_json(sidecar_path.parent / "review-video.json") if sidecar else {}
    valid_video = bool(video.get("episode_uuid") == uid and video.get("state") == "ready"
                       and sidecar.get("capture_state") == "committed" and video_path.is_file()
                       and not video_path.is_symlink())
    if valid_video:
        try:
            video_path.resolve().relative_to((Path(spec["data_root"]) / ".segments").resolve())
        except (ValueError, OSError):
            valid_video = False
    started, ended = finished.get("started_at"), finished.get("finished_at")
    duration = ended - started if finite(started) and finite(ended) and ended >= started else None
    summary = cached_summary or {}
    hil_steps = summary.get("hil_steps")
    hil = hil_steps > 0 if type(hil_steps) is int and hil_steps >= 0 else None
    hil_source = "journal_valid_rows" if hil is not None else None
    if hil is None and type(sidecar.get("has_hil")) is bool:
        hil, hil_source = sidecar["has_hil"], "recorder_sidecar"
    outcome = finished.get("outcome", sidecar.get("episode_outcome"))
    if outcome not in ("success", "failure", "unknown", "aborted"):
        outcome = None
    return dict(
        video_url=("/api/segmented-teach/episodes/" + uid + "/preview.mp4?" +
                   urlencode({"data_root": spec["data_root"]})) if valid_video else None,
        alignment=alignment(video, rollout) if valid_video else
            dict(status="unavailable", basis="No completed review video", segments=[]),
        outcome=outcome, duration_seconds=duration, duration_basis="episode_wall_time_including_pauses",
        hil=hil, hil_source=hil_source, hil_steps=hil_steps,
        options=spec.get("options"), actor_version=finished.get("actor_version", rollout.get("actor_version")),
        training_eligible=finished.get("training_eligible"), transition_count=finished.get("count"))


def read_run(spec, cache_root=None, limit=300, include_rollouts=False,
             success_series=None, strict_archive=False):
    """Only inspect explicitly registered local run/data roots."""
    root = Path(spec["run_dir"])
    key = hashlib.sha256(str(root).encode()).hexdigest()[:20]
    rollouts = read_rollouts(root, limit, strict=strict_archive)
    finished = {}
    stamps = {}
    for path in (root / "episodes").glob("*/finished.json"):
        uid = path.parent.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", uid):
            continue
        value = read_json(path)
        if value.get("episode_uuid") == uid:
            finished[uid] = value
            try:
                stamps[uid] = path.stat().st_mtime_ns
            except OSError:
                pass
    ordered = sorted(finished, key=lambda uid: (finished[uid].get("finished_at") or 0, uid))
    numbers = {uid: i for i, uid in enumerate(ordered, 1)}
    cached = read_json(Path(cache_root) / (key + ".json")).get("episodes", {}) if cache_root else {}
    sidecars = find_sidecars(spec["data_root"])
    identities = sorted(set(rollouts) | set(ordered[-limit:]),
                        key=lambda uid: (finished.get(uid, {}).get("finished_at") or rollouts.get(uid, {}).get("started_at") or 0, uid),
                        reverse=True)[:limit]
    entries, outcomes, summaries = [], [], []
    for uid in ordered:
        record = cached.get(uid, {})
        summary = record.get("summary") if record.get("stamp") == stamps.get(uid) else None
        summaries.append(dict(finished[uid], hil_steps=(summary or {}).get("hil_steps")))
    for uid in identities:
        # URL path IDs are constrained independently of on-disk metadata.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", uid):
            continue
        record = cached.get(uid, {})
        summary = record.get("summary") if record.get("stamp") == stamps.get(uid) else None
        review = review_for(uid, rollouts.get(uid, {}), finished.get(uid, {}), sidecars.get(uid), spec, summary)
        rollout = rollouts.get(uid, {})
        points = rollout.get("points") or []
        started = rollout.get("started_at", finished.get(uid, {}).get("started_at"))
        if not finite(started) and points:
            first = points[0]
            if finite(first.get("timestamp")) and finite(first.get("seconds")):
                started = first["timestamp"] - first["seconds"]
        entry = dict(episode_uuid=uid, review=review, started_at=started,
                     actor_version=review["actor_version"], count=len(points))
        if points:
            entry["rollout_url"] = "/rollout-review/" + key + "/" + uid + ".json"
            if include_rollouts:
                entry["_rollout"] = dict(rollout, review=review, started_at=started,
                                          historical=True, active=False)
        entries.append(entry)
        if uid in finished:
            outcomes.append(dict(episode=numbers[uid], episode_uuid=uid, **{
                k: review[k] for k in ("duration_seconds", "outcome", "hil", "actor_version",
                                       "training_eligible", "options", "transition_count")},
                                 assisted=review["hil"]))
    outcomes.sort(key=lambda item: item["episode"])
    available = [entry for entry in entries if entry.get("rollout_url")]
    latest = max(available, key=lambda entry: entry.get("started_at") or 0) if available else None
    success = success_series(summaries) if success_series is not None else dict(completed=len(ordered))
    return dict(model_id=spec["model_id"], options=spec.get("options"), run_key=key,
                episodes=entries, latest_episode_uuid=latest["episode_uuid"] if latest else None,
                training=read_json(root / "telemetry/learner.json"),
                success=dict(success, episodes=outcomes))


def catalog_runs(catalog, data_root=None):
    """Extract registered defaults and existing options directories; no imports."""
    result = []
    for model in catalog.get("models", []):
        root = model.get("online_run_dir")
        data = (model.get("data_directories") or {}).get("collection")
        if model.get("runtime_adapter") != "expo_online" or not root or not data:
            continue
        if "{DATA}" in data:
            if data_root is None:
                continue
            data = data.replace("{DATA}", str(data_root))
        options = (model.get("expo_online_options") or {}).get("defaults")
        # Some fixed HPC entries register the same values in model metadata.
        if options is None:
            options = {k: model[k] for k in ("rtc_delay", "edit_scale") if k in model}
            options = options if len(options) == 2 else None
        # Unknown fixed-profile values remain null. run_key still isolates their
        # registered run and lets the UI match an API response with options=null.
        spec = dict(model_id=model["id"], run_dir=root, data_root=data, options=options)
        if Path(root).is_dir():
            result.append(spec)
        for variant in sorted(Path(root).parent.glob(Path(root).name + "--d*-edit*")):
            match = re.fullmatch(re.escape(Path(root).name) + r"--d([1-8])-edit(00[1-9]|010)", variant.name)
            if match and variant.is_dir():
                result.append(dict(spec, run_dir=str(variant),
                                   options=dict(rtc_delay=int(match[1]), edit_scale=int(match[2]) / 100.)))
    return result
