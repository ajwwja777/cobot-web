from types import SimpleNamespace
from fastapi.testclient import TestClient
from segmented_capture import api
from tests.test_segmented_capture_http import FakeService,FakeBridge

class Preview:
    def __init__(self,path):self.path=path
    def queue(self,a):return {"state":"ready","episode_uuid":a.episode_uuid}
    def preview_path(self,a):return self.path
    def is_busy(self,uuid):return False
    def forget(self,uuid):pass
    def shutdown(self):pass

def test_segmented_preview_and_range(project_tmp,monkeypatch):
    service=FakeService(project_tmp);service.capture_state="committed"
    path=project_tmp/"preview.mp4";path.write_bytes(b"0123456789")
    store=SimpleNamespace(get_episode_artifacts=lambda uuid:SimpleNamespace(episode_uuid=str(uuid)))
    service.replay_artifacts=lambda uuid, data_root=None:store.get_episode_artifacts(uuid)
    app=api.create_app(service=service,bridge=FakeBridge(),preview_manager=Preview(path))
    with TestClient(app) as client:
        base=f"/api/segmented-teach/episodes/{service.uuid}"
        assert client.get(base+"/preview").json()["state"]=="ready"
        response=client.get(base+"/preview.mp4",headers={"Range":"bytes=0-3"})
        assert response.status_code==206
        assert response.content==b"0123"
        service.capture_state="recording"
        assert client.get(base+"/preview").status_code==409


def test_ready_preview_state_exposes_timing_metadata(tmp_path):
    from uuid import UUID
    from capture_core.labels import EpisodeArtifacts
    from capture_core.preview_jobs import PreviewJobManager
    artifact = EpisodeArtifacts(
        episode_uuid=UUID("00000000-0000-0000-0000-000000000001"),
        episode_index=1,
        episode_path=tmp_path / "episode.hdf5",
        sidecar_path=tmp_path / "episode.labels.json",
        preview_directory=tmp_path / ".previews" / "uuid",
        frame_count=150,
        size_bytes=10,
    )
    metadata = {
        "output_frame_count": 75,
        "source_frame_count": 150,
        "source_fps": 30.0,
        "output_fps": 15.0,
    }
    manager = PreviewJobManager(
        validator=lambda *_args: metadata,
        generator=lambda *_args, **_kwargs: metadata,
    )
    try:
        state = manager.queue(artifact)
    finally:
        manager.shutdown()
    assert state["state"] == "ready"
    assert state["source_frame_count"] == 150
    assert state["source_fps"] == 30.0
    assert state["output_fps"] == 15.0
