from capture_core.api import _public_status


def test_recorder_timing_is_numeric_allowlisted_and_optional():
    result = _public_status({"state":"error", "timing":{
        "queue_depth":256, "writer_inflight_ms":32000.5,
        "queue_capacity":256, "writer_max_ms":float("nan"),
        "sample_last_ms":-1, "sample_max_ms":True,
        "path":"/private/file", "error":"private exception"}})
    assert result["timing"] == {"queue_depth":256, "queue_capacity":256,
                                "writer_inflight_ms":32000.5}
    assert "timing" not in _public_status({"state":"idle"})
