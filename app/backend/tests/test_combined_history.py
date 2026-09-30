from types import SimpleNamespace
import hashlib
from capture_core.history import combined_history
from tests.test_labels import _write_episode

def test_mixed_directory_deduplicates_normal_hdf5_and_preserves_readers(tmp_path):
    raw_path, raw_id = _write_episode(tmp_path, index=1)
    normal_path, normal_id = _write_episode(tmp_path, index=2)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw_path, normal_path)}
    service = SimpleNamespace(list_episodes=lambda **kw: [
        {"episode_uuid": str(normal_id), "episode_index": 2, "node_count": 2}])
    result = combined_history(tmp_path, service, limit=10)
    assert result["total"] == 2
    assert [r["history_format"] for r in result["episodes"]] == ["segmented", "rollout"]
    assert result["episodes"][1]["episode_uuid"] == str(raw_id)
    assert combined_history(tmp_path, service, limit=1, offset=1)["episodes"] == result["episodes"][1:]
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}

def test_normal_nodes_keep_actual_label_and_labelability(tmp_path):
    from capture_core.labels import LabelStore
    _, uuid = _write_episode(tmp_path, index=3)
    store = LabelStore(tmp_path)
    store.set_outcome(uuid, 'success')
    service = SimpleNamespace(list_episodes=lambda **kw:[dict(episode_uuid=str(uuid),episode_index=3,node_count=2)])
    row = combined_history(tmp_path, service)['episodes'][0]
    assert row['episode_outcome']=='success' and row['labelable'] and row['has_labels']
    store.set_outcome(uuid, 'failure')
    assert combined_history(tmp_path, service)['episodes'][0]['episode_outcome']=='failure'
