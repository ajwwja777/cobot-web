from pathlib import Path
from types import SimpleNamespace
import numpy as np
from fastapi.testclient import TestClient
from cobot_console.api import create_app
from capture_core.ros_cache import LatestMessageCache
from tests.test_console_api import FakeBridge,FakeRecorder,FakeSegmentedService,FakeBackend,FakeRegistry


def test_frontend_entry_revalidates_and_camera_nodes_do_not_mask_no_frames(tmp_path):
    devices=SimpleNamespace(runtime=tmp_path,status=lambda:{'jobs':{},'systems':{'cameras':{'phase':'ready','detail':'3/3 camera nodes'}}})
    cache=LatestMessageCache()
    app=create_app(cache=cache,bridge=FakeBridge(),recorder=FakeRecorder(),segmented_service=FakeSegmentedService(),backend_client=FakeBackend(),lifecycle_registry=FakeRegistry(),allowed_data_root=tmp_path,rlt_data_root=tmp_path,device_controller=devices,monotonic=lambda:10)
    with TestClient(app) as client:
        response=client.get('/')
        assert response.headers['cache-control'].startswith('no-store')
        assert response.headers['x-cobot-ui-revision']=='20261115-replay-quality'
        assert 'motion_ui.js?v=20261115-replay-quality' in response.text
        state=client.get('/api/console/devices').json()['systems']['cameras']
        assert state['phase']=='error' and state['node_phase']=='ready' and state['fresh_streams']==0
        for key in ['camera_left','camera_high','camera_right']:cache.put(key,np.zeros((12,16,3),dtype=np.uint8),source_stamp=10,arrival_stamp=10)
        state=client.get('/api/console/devices').json()['systems']['cameras']
        assert state['phase']=='ready' and state['fresh_streams']==3
