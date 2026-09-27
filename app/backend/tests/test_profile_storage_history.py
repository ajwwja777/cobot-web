import json

from cobot_console import profile_storage


def test_plug_v3_history_reads_finalized_labels_and_hil_without_opening_hdf5(tmp_path):
    def write_episode(index, outcome, interventions, with_hdf5=True):
        stem = f"episode_{index:06d}"
        (tmp_path / f"{stem}.labels.json").write_text(json.dumps({
            "episode_uuid": f"uuid-{index}",
            "episode_outcome": outcome,
            "interventions": interventions,
        }))
        if with_hdf5:
            (tmp_path / f"{stem}.hdf5").write_bytes(b"finalized")

    write_episode(1, "success", [{"side": "right"}])
    write_episode(2, "failure", [])
    write_episode(3, "aborted", [], with_hdf5=False)

    episodes = profile_storage.history(tmp_path)

    assert [(item["episode_index"], item["outcome"], item["hil_frames"]) for item in episodes] == [
        (1, "success", 1),
        (2, "failure", 0),
    ]
