import pytest
import json,socket,threading,time,subprocess,os
from pathlib import Path
from urllib.request import ProxyHandler,Request,build_opener
import uvicorn
from cobot_console.api import create_app
from capture_core.hdf5_writer import Hdf5EpisodeWriter
from capture_core.sampler import FrameSampler
from capture_core.labels import LabelStore
from tests.test_console_api import _fresh_cache,FakeBridge,FakeRecorder,FakeSegmentedService,FakeBackend,FakeRegistry

@pytest.mark.skipif(not os.environ.get("COBOT_RLT_TEST_PYTHON"), reason="Set COBOT_RLT_TEST_PYTHON for algorithm integration")
def test_flat_real_http_start_finalize_restart_with_native_hdf5(tmp_path,monkeypatch):
    monkeypatch.delenv('COBOT_RLT_MODEL_MANIFEST',raising=False)
    monkeypatch.setenv('COBOT_RECORDING_LAYOUT','flat')
    cache=_fresh_cache()
    class Recorder(FakeRecorder):
        def start(self,request):
            assert request.identity.storage_layout=='flat',repr(request.identity)
            super().start(request)
            self.writer=Hdf5EpisodeWriter(request.data_root,request.identity,4)
            self.writer.append(FrameSampler().sample(cache.snapshot(10.),request.identity,0))
            return self.status()
        def stop(self):
            self.writer.finalize();self.state='stopped';return self.writer.final_path
    recorder=Recorder()
    app=create_app(cache=cache,bridge=FakeBridge(),recorder=recorder,
        segmented_service=FakeSegmentedService(),backend_client=FakeBackend(),
        lifecycle_registry=FakeRegistry(),allowed_data_root=tmp_path,rlt_data_root=tmp_path,
        monotonic=lambda:10.)
    sock=socket.socket();sock.bind(('127.0.0.1',0));base='http://127.0.0.1:'+str(sock.getsockname()[1])
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',timeout_graceful_shutdown=1))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        until=time.monotonic()+3
        while not server.started and time.monotonic()<until:time.sleep(.01)
        assert server.started
        op=build_opener(ProxyHandler({}));req=Request(base+'/api/console/mode',data=b'{"mode":"rlt"}',headers={'Content-Type':'application/json'},method='POST')
        with op.open(req,timeout=3) as response:assert response.status==200
        script = "from methods.openpi_rlt.plug_v2.recorder_client import FlatRecorderClient\nfrom methods.openpi_rlt.cobot_adapter.task5_client import Task5EpisodeIdentity\nfrom methods.openpi_rlt.cobot_adapter.trace import EpisodeOutcome\nclient=FlatRecorderClient(" + repr(base+'/api/rlt-recorder') + ",timeout_sec=3)\nidentity=Task5EpisodeIdentity('plug_insertion','plug_v2_reference','step_4000','plug_v2',"+repr(str(tmp_path))+",4)\nfor index,outcome in enumerate((EpisodeOutcome.SUCCESS,EpisodeOutcome.FAILURE,EpisodeOutcome.ABORTED)):\n ref=client.start_episode(identity);assert ref.episode_index==index+1\n finished=client.finish_episode(ref,outcome);assert finished.episode_uuid\nprint('FLAT_CLIENT_SUCCESS')\n"
        result=subprocess.run([os.environ['COBOT_RLT_TEST_PYTHON'],'-c',script],env=os.environ.copy(),capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stdout+result.stderr
        assert 'FLAT_CLIENT_SUCCESS' in result.stdout
        for index in range(1,4):assert (tmp_path/f'episode_{index:06d}.hdf5').is_file()
        with op.open(base+'/api/console/status',timeout=3) as response:
            assert json.load(response)['active_mode'] is None
        episodes=LabelStore(tmp_path).list_episodes();assert len(episodes)==3
        assert not (tmp_path/'plug_insertion').exists()
    finally:
        server.should_exit=True;thread.join(timeout=4);sock.close()
    assert not thread.is_alive()
