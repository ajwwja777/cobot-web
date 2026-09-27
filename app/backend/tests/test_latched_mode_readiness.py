from types import SimpleNamespace
import numpy as np
from capture_core.readiness import evaluate_readiness
from capture_core.ros_cache import LatestMessageCache
from capture_core.ros_subscriber import RosSubscriberBridge, RosBindings

def snapshot(mode='policy', teach_stamp=9.95):
    cache = LatestMessageCache()
    for key in ('camera_high', 'camera_left', 'camera_right'):
        cache.put(key, np.zeros((2,2,3),dtype=np.uint8), 9.95, 9.95)
    if mode is not None:
        cache.put('handover_mode', mode, 1.0, 1.0)
    for key in ('teach_left', 'teach_right'):
        cache.put(key, False, teach_stamp, teach_stamp)
    return cache.snapshot(10.0)

def test_unchanged_latched_mode_is_not_a_heartbeat():
    assert evaluate_readiness({'state':'ready'}, snapshot())['status'] == 'ok'

def test_missing_latched_mode_still_blocks_start():
    r=evaluate_readiness({'state':'ready'}, snapshot(mode=None))
    assert r['error_code']=='handover_stale'
    assert r['stale_keys']==['handover_mode']

def test_empty_latched_mode_is_not_ready():
    assert evaluate_readiness({'state':'ready'}, snapshot(mode=''))['status']=='not_ready'

def test_live_teach_signals_still_expire():
    r=evaluate_readiness({'state':'ready'}, snapshot(teach_stamp=8.0))
    assert r['error_code']=='handover_stale'
    assert r['stale_keys']==['teach_left','teach_right']

def test_dead_mode_publisher_blocks_even_with_cached_mode():
    ros=SimpleNamespace(
        core=SimpleNamespace(is_initialized=lambda:True),
        Subscriber=lambda *a,**kw:SimpleNamespace(unregister=lambda:None),
    )
    bridge=RosSubscriberBridge(
        LatestMessageCache(),
        bindings_loader=lambda:RosBindings(ros,lambda:None,object,object,object,object),
        master_probe=lambda:True,
        registration_probe=lambda *a:True,
        mode_publisher_probe=lambda *a:False,
    )
    bridge.start()
    assert bridge.status()=={'state':'not_ready','error_code':'handover_publisher_unavailable'}

def test_master_registry_alone_cannot_prove_mode_publisher_alive(monkeypatch):
    from capture_core import ros_subscriber as m
    class Master:
        def getSystemState(self, node):
            return (1,'',[[['/task2/teach_handover/mode',['/coordinator']]],[],[]])
        def lookupNode(self, caller,node):
            return (1,'','http://coordinator:1234')
    class Node:
        def getPid(self, caller):
            raise OSError('dead publisher')
    monkeypatch.setattr(m.xmlrpc.client,'ServerProxy',
        lambda uri,**kw: Master() if '11311' in uri else Node())
    assert not m._ros_mode_publisher_live('http://master:11311','/recorder')

def test_live_mode_publisher_passes_bounded_probe(monkeypatch):
    from capture_core import ros_subscriber as m
    class Master:
        def getSystemState(self,node):
            return (1,'',[[['/task2/teach_handover/mode',['/coordinator']]],[],[]])
        def lookupNode(self,caller,node):
            return (1,'','http://coordinator:1234')
    class Node:
        def getPid(self,caller):
            return (1,'',123)
    monkeypatch.setattr(m.xmlrpc.client,'ServerProxy',
        lambda uri,**kw: Master() if '11311' in uri else Node())
    assert m._ros_mode_publisher_live('http://master:11311','/recorder')
