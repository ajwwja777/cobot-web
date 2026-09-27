import importlib.util
import sys
import threading
from pathlib import Path

ROBOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROBOT))
spec = importlib.util.spec_from_file_location('recover_front_pair_cli', ROBOT / 'recover.py')
recover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recover)


def test_front_pair_preflights_and_recovers_concurrently(monkeypatch):
    import front_mode
    import front_reset
    import sync_recovery

    preflight = []
    barrier = threading.Barrier(2, timeout=1)
    active = []
    monkeypatch.setattr(recover.rospy.core, 'is_initialized', lambda: True)
    monkeypatch.setattr(front_mode, 'operator_guard', lambda side, **kw: preflight.append(side))
    monkeypatch.setattr(sync_recovery, 'recover_sync', lambda: active.append('sync'))
    def resume(side, **_kwargs):
        assert preflight == ['left', 'right']
        barrier.wait()
        active.append(side)
        return {'ready': True}
    monkeypatch.setattr(front_reset, 'recover_front', resume)
    monkeypatch.setattr(sys, 'argv', ['recover.py', 'front-pair', '--supported'])
    assert recover.main() == 0
    assert set(active[:2]) == {'left', 'right'}
    assert active[-1] == 'sync'
