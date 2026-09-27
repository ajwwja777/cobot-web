import json,signal,time
from threading import Event,Thread
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from capture_core.recorder import RolloutRecorder,RecorderStartRequest,RecorderError
from tests.test_recorder import _config,_ready_cache,_identity,_CancellableWriter,_UncancellableWriter,DiskUsage,GIB
from tests.test_rollout_ui_contract import fixture_app
try:
    from methods.openpi_rlt.plug_v2 import cli,terminal
except ImportError:
    cli = terminal = None



def make_recorder(root,writer,drain=.25):
    writer.instances.clear()
    return RolloutRecorder(_config(root,capacity=100),_ready_cache(),writer_factory=writer,
       disk_usage=lambda _:DiskUsage(100*GIB,50*GIB,50*GIB),shutdown_timeout_seconds=.02,drain_timeout_seconds=drain)


def test_slow_append_drains_beyond_short_shutdown_without_cancel(project_tmp):
    recorder=make_recorder(project_tmp,_CancellableWriter)
    recorder.start(RecorderStartRequest(_identity(1),max_timesteps=1))
    writer=_CancellableWriter.instances[-1];assert writer.appended.wait(1)
    done=Thread(target=lambda:(time.sleep(.09),writer.release.set()));done.start()
    path=recorder.stop();done.join()
    assert recorder.status()['state']=='stopped' and path==writer.final_path
    assert writer.cancel_reason is None and writer.abort_reason is None


def test_failed_worker_must_exit_before_recovery_and_never_publish(project_tmp):
    recorder=make_recorder(project_tmp,_UncancellableWriter,drain=.03)
    recorder.start(RecorderStartRequest(_identity(2),max_timesteps=1))
    writer=_UncancellableWriter.instances[-1];assert writer.appended.wait(1)
    with pytest.raises(RecorderError):recorder.stop()
    with pytest.raises(RecorderError,match='still alive'):recorder.recover_error()
    writer.release.set()
    limit=time.monotonic()+2
    while recorder.status()['writer_thread_alive'] and time.monotonic()<limit:time.sleep(.005)
    assert recorder.recover_error()['state']=='idle'
    assert writer.incomplete_path.exists() and not writer.final_path.exists()
    recorder.start(RecorderStartRequest(_identity(3),max_timesteps=1))
    writer=_UncancellableWriter.instances[-1];writer.release.set();recorder.stop()


def test_recovery_requires_stopped_session(project_tmp,monkeypatch):
    client,storage,backend=fixture_app(project_tmp,monkeypatch)
    backend.phase='paused'
    assert client.post('/api/rlt/recover-recorder',json={}).status_code==409
    backend.phase='stopped'
    assert client.post('/api/rlt/recover-recorder',json={}).status_code==200


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_stop_from_fault_skips_pause_and_homing_and_waits_stopped(monkeypatch):
    calls=[];phase=['fault']
    monkeypatch.setattr(cli,'occupied',lambda _:True)
    def http(path,body=None):
        calls.append((path,body))
        if body is not None:phase[0]='stopped'
        return {'phase':phase[0],'episode_id':3,'generation':24}
    monkeypatch.setattr(cli,'http',http);cli.stop_session()
    assert calls==[('/api/session',None),('/api/session/stop',{'episode_id':3,'generation':24}),('/api/session',None)]


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_foreground_interrupt_stops_only_registered_session_keeps_model(project_tmp,monkeypatch):
    monkeypatch.setattr(cli,'BACKEND',project_tmp)
    owner={'pid':123,'start_ticks':456};calls=[]
    monkeypatch.setattr(terminal,'registered_session',lambda:owner)
    monkeypatch.setattr(cli,'end_session',lambda:calls.append('end_session'))
    monkeypatch.setattr(cli,'occupied',lambda _:True)
    terminal.stop_owned(owner,None)
    assert calls==['end_session'] and not (project_tmp/'request.json').exists()


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_old_terminal_does_not_stop_replacement_session(project_tmp,monkeypatch):
    monkeypatch.setattr(cli,'BACKEND',project_tmp);calls=[]
    monkeypatch.setattr(terminal,'registered_session',lambda:{'pid':999,'start_ticks':1000})
    monkeypatch.setattr(cli,'end_session',lambda:calls.append('end_session'))
    terminal.stop_owned({'pid':123,'start_ticks':456},None)
    assert calls==[]


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_interrupt_during_model_load_cancels_future_session_only(project_tmp,monkeypatch):
    monkeypatch.setattr(cli,'BACKEND',project_tmp);calls=[]
    monkeypatch.setattr(terminal,'registered_session',lambda:None)
    monkeypatch.setattr(cli,'end_session',lambda:calls.append('end_session'))
    monkeypatch.setattr(cli,'occupied',lambda _:False)
    terminal.stop_owned(None,None)
    assert calls==['end_session'] and json.loads((project_tmp/'request.json').read_text())=={'command':'session-stop'}


@pytest.mark.parametrize('sig',[signal.SIGINT,signal.SIGHUP,signal.SIGTERM])
@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_terminal_signals_route_to_session_stop(monkeypatch,project_tmp,sig):
    monkeypatch.setattr(cli,'BACKEND',project_tmp);cli.atomic(project_tmp/'state.json',{'phase':'ready_disarmed'})
    owner={'pid':123,'start_ticks':456};calls=[];handlers={}
    monkeypatch.setattr(terminal,'registered_session',lambda:owner)
    monkeypatch.setattr(cli,'occupied',lambda _:True)
    monkeypatch.setattr(cli,'http',lambda _: {'phase':'disarmed'})
    monkeypatch.setattr(terminal,'stop_owned',lambda owner,previous:calls.append((owner,previous)))
    monkeypatch.setattr(terminal.signal,'signal',lambda s,f:handlers.setdefault(s,f))
    def interrupt(_):handlers[sig](sig,None)
    monkeypatch.setattr(terminal.time,'sleep',interrupt)
    terminal.watch(None)
    assert calls==[(owner,None)]


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_supervisor_pending_session_stop_keeps_model_loading(project_tmp,monkeypatch):
    monkeypatch.setattr(cli,'BACKEND',project_tmp)
    cli.atomic(project_tmp/'request.json',{'command':'session-stop'})
    s=cli.Supervisor.__new__(cli.Supervisor);s.stop=False;s.args=NS(preload=False)
    assert s.stopping_requested() is False and s.args.preload is True


@pytest.mark.skipif(cli is None, reason="RLT algorithm dependency not installed")
def test_orphaned_paused_session_requires_explicit_restart(project_tmp,monkeypatch):
    import sys
    monkeypatch.setattr(cli,'BACKEND',project_tmp)
    manifest=project_tmp/'manifest.json';manifest.write_text(json.dumps({'status':'offline_validated'}));monkeypatch.setattr(cli,'MANIFEST',manifest)
    cli.atomic(project_tmp/'processes.json',{'supervisor':{'pid':1,'start_ticks':2},'children':{'session':{'pid':3,'start_ticks':4}}})
    monkeypatch.setattr(cli,'alive',lambda _:True);monkeypatch.setattr(cli,'occupied',lambda _:True)
    phase=['paused'];calls=[]
    monkeypatch.setattr(cli,'http',lambda _: {'phase':phase[0]})
    monkeypatch.setattr(sys,'argv',['cli','up','--actor','reference'])
    with pytest.raises(RuntimeError,match='rlt_stop'):cli.main()
    assert not (project_tmp/'request.json').exists()
    def stop():calls.append('end_session');phase[0]='stopped'
    monkeypatch.setattr(cli,'end_session',stop)
    monkeypatch.setattr(sys,'argv',['cli','up','--actor','reference','--restart'])
    cli.main()
    assert calls==['end_session'] and json.loads((project_tmp/'request.json').read_text())['command']=='up'


def test_native_hdf_slow_drain_is_saved_and_readable(project_tmp):
    import h5py
    from capture_core.hdf5_writer import Hdf5EpisodeWriter
    class SlowNative(Hdf5EpisodeWriter):
        entered=Event();release=Event()
        def append(self,frame):
            self.entered.set();assert self.release.wait(1);return super().append(frame)
    recorder=make_recorder(project_tmp,SlowNative,drain=.5) if hasattr(SlowNative,'instances') else RolloutRecorder(
        _config(project_tmp,capacity=100),_ready_cache(),writer_factory=SlowNative,
        disk_usage=lambda _:DiskUsage(100*GIB,50*GIB,50*GIB),shutdown_timeout_seconds=.02,drain_timeout_seconds=.5)
    recorder.start(RecorderStartRequest(_identity(90),max_timesteps=1))
    assert SlowNative.entered.wait(1)
    worker=Thread(target=lambda:(time.sleep(.1),SlowNative.release.set()));worker.start()
    path=recorder.stop();worker.join()
    assert recorder.status()['state']=='stopped' and recorder.status()['last_error'] is None
    with h5py.File(path,'r') as h:assert h.attrs['completion_state']=='complete' and len(h['observations/qpos'])==1
