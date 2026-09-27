import json
import socket
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener
import uvicorn
from cobot_console.api import create_app
from cobot_console.rlt_proxy import BackendResponse, RltBackendError
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("console_api_fakes",
    Path(__file__).with_name("test_console_api.py"))
fakes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fakes)
FakeBridge, FakeRecorder = fakes.FakeBridge, fakes.FakeRecorder
FakeSegmentedService, FakeRegistry = fakes.FakeSegmentedService, fakes.FakeRegistry
_fresh_cache = fakes._fresh_cache

def test_start_proxy_can_call_recorder_on_same_event_loop(tmp_path):
    # Real loopback HTTP is needed: a fake successful upstream misses reentry.
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    base='http://127.0.0.1:%d' % sock.getsockname()[1]
    op=build_opener(ProxyHandler({}))
    callbacks=[]
    class Backend:
        def request(self,method,path,body=None):
            try:
                with op.open(base+'/api/rlt-recorder/healthz',timeout=0.5) as r:
                    callbacks.append(json.load(r))
            except OSError as error:
                raise RltBackendError('recorder_reentry_timeout') from error
            return BackendResponse(200,{'phase':'rollout','policy_paused':False})
    app=create_app(cache=_fresh_cache(),bridge=FakeBridge(),recorder=FakeRecorder(),
        segmented_service=FakeSegmentedService(),backend_client=Backend(),
        lifecycle_registry=FakeRegistry(),allowed_data_root=tmp_path,
        rlt_data_root=tmp_path,monotonic=lambda:10.0)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',
        timeout_graceful_shutdown=1))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    def post(path,payload):
        req=Request(base+path,data=json.dumps(payload).encode(),
            headers={'Content-Type':'application/json'},method='POST')
        return op.open(req,timeout=3)
    try:
        deadline=time.monotonic()+3
        while not server.started and time.monotonic()<deadline:time.sleep(0.01)
        assert server.started
        with post('/api/console/mode',{'mode':'rlt'}) as r:assert r.status==200
        with post('/api/rlt/session/start',{'episode_id':0,'generation':1}) as r:
            assert r.status==200
            assert json.load(r)['phase']=='rollout'
        assert callbacks==[{'status':'ok','error_code':None,'stale_keys':[]}]
    finally:
        server.should_exit=True
        thread.join(timeout=4)
        sock.close()
    assert not thread.is_alive()
