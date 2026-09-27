import json
import os

from segmented_capture.capture_service import SegmentedCaptureService


def test_recent_history_page_reads_only_requested_sidecars(tmp_path):
    for index in range(7):
        episode = tmp_path / ("episode-%02d" % index)
        episode.mkdir()
        (episode / "sidecar.json").write_text(
            json.dumps({
                "episode_index": index,
                "commit_state": "committed",
                "capture_state": "committed",
                "training_frame_count": index * 10,
                "nodes": [{"sample_timestamp": float(index)}],
            }),
            encoding="utf-8",
        )
        os.utime(episode / "sidecar.json", (100 + index, 100 + index))

    service = SegmentedCaptureService.__new__(SegmentedCaptureService)
    service._selected_sidecar_root = lambda _data_root=None: tmp_path
    page = service.list_episodes(data_root=tmp_path, limit=3, offset=2)

    assert [item["episode_index"] for item in page] == [4, 3, 2]
