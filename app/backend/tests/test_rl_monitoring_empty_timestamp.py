import json
import pytest
from cobot_console import rl_monitoring as module


@pytest.mark.parametrize('local_stamp,remote_stamp,expected', [
    (None, 1, 'remote'), (1, None, 'local'), (None, None, 'remote'), (2, 1, 'local'),
])
def test_empty_history_timestamps_keep_read_only_history_available(tmp_path, monkeypatch, local_stamp, remote_stamp, expected):
    root = tmp_path / 'run'
    (root / 'telemetry').mkdir(parents=True)
    local = dict(updated_at=local_stamp, points=[], source='local')
    remote = dict(updated_at=remote_stamp, points=[], source='remote')
    (root / 'telemetry/learner.json').write_text(json.dumps(local))
    def read(path):
        try:
            return json.loads(path.read_text())
        except FileNotFoundError:
            return {}
    monkeypatch.setattr(module, 'contract', lambda: {'read_json': read})
    reader = module.MonitoringReader(tmp_path / 'cache')
    reader.scan = lambda *args: dict(points=[])
    reader.remote_read = lambda *args: (remote, dict(supported=True, connected=False, running=False))
    try:
        result = reader.read(dict(id='test', runtime_adapter='expo_online', online_run_dir=str(root)), dict(phase='offline'))
        assert result['training']['source'] == expected
        assert result['live'] is False
    finally:
        reader.close()
