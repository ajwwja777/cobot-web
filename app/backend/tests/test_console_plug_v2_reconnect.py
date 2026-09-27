import time
from cobot_console.ros_bridge import ReconnectingBridge
from capture_core.ros_cache import LatestMessageCache
from tests.test_ros_subscriber import FakeRospy,_bindings


def test_start_before_ros_then_reconnect_and_invalidate_old_cache():
 cache=LatestMessageCache();ros=FakeRospy(initialized=True);master={'ready':False}
 bridge=ReconnectingBridge(cache,bindings_loader=lambda:_bindings(ros),master_probe=lambda:master['ready'],registration_probe=lambda *_:True,mode_publisher_probe=lambda *_:True,retry_seconds=.01)
 def wait(fn):
  deadline=time.monotonic()+1
  while time.monotonic()<deadline:
   if fn():return
   time.sleep(.005)
  raise AssertionError(bridge.status())
 try:
  bridge.start();assert bridge.status()['state']=='not_ready'
  master['ready']=True;wait(lambda:bridge.status()['state']=='ready')
  assert len(ros.subscriptions)==15
  cache.put('handover_mode','policy',1.,1.)
  master['ready']=False
  wait(lambda:all(s.unregistered for s in ros.subscriptions))
  assert cache.snapshot(1.).get('handover_mode') is None
  master['ready']=True;wait(lambda:bridge.status()['state']=='ready')
  assert len(ros.subscriptions)==30
 finally:bridge.shutdown()
 assert all(s.unregistered for s in ros.subscriptions)


def test_missing_handover_publisher_keeps_camera_subscriptions_and_cache():
 cache=LatestMessageCache();ros=FakeRospy(initialized=True)
 bridge=ReconnectingBridge(cache,bindings_loader=lambda:_bindings(ros),master_probe=lambda:True,registration_probe=lambda *_:True,mode_publisher_probe=lambda *_:False,retry_seconds=.01)
 try:
  bridge.start()
  assert bridge.status()=={'state':'not_ready','error_code':'handover_publisher_unavailable'}
  cache.put('camera_left',object(),1.,1.)
  time.sleep(.05)
  assert not any(s.unregistered for s in ros.subscriptions)
  assert cache.snapshot(1.).get('camera_left') is not None
 finally:bridge.shutdown()