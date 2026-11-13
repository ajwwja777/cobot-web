import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cobot_console.motion import MotionManager, install_routes, display_name, VideoArchive
from cobot_console.mode import RecorderModeCoordinator
from cobot_console.deployment import DeploymentManager, DeploymentError


def make(tmp_path, phase="offline", popen=None):
    from cobot_console.motion import MotionManager
    from tests.test_deployment_evaluation import Runtime,Cameras,MODEL
    runtime=Runtime();runtime.phase=phase
    modes=RecorderModeCoordinator()
    dep=DeploymentManager(Cameras(),runtime=runtime,directory=tmp_path/"deploy",allowed_root=tmp_path,model_provider=lambda:[MODEL],busy=lambda:modes.snapshot().active_mode is not None)
    def resolve(value,create=False):
        p=Path(value).resolve()
        if tmp_path!=p and tmp_path not in p.parents:raise ValueError("outside")
        if create:p.mkdir(parents=True,exist_ok=True)
        return p
    motion=MotionManager(dep,modes,resolve,directory=tmp_path/"runtime",**({"popen":popen} if popen else {}));dep.motion=motion
    motion.default_root=tmp_path/"traces"
    return dep,modes,motion


def wait_done(motion):
    end=time.monotonic()+3
    while motion.busy() and time.monotonic()<end:time.sleep(.01)
    end=time.monotonic()+3
    while motion.state.get("phase")=="stopping" and time.monotonic()<end:time.sleep(.01)


def test_record_process_identity_stale_stop_and_single_writer(tmp_path):
    commands=[]
    def popen(args,**kwargs):
        commands.append(args)
        return subprocess.Popen([sys.executable,"-u","-c","import sys; print('ready'); assert sys.stdin.readline().strip()=='stop'"],**kwargs)
    dep,modes,motion=make(tmp_path,popen=popen)
    try:
        result=motion.start("record",name="测试轨迹 / 不作为路径")
        assert result["active"] and modes.snapshot().writer_token
        assert commands[0][-1]=="--web-confirmed" and "测试轨迹 / 不作为路径" in commands[0]
        with pytest.raises(ValueError):motion.stop("old")
        with pytest.raises(ValueError):motion.start("record",name="second")
        with pytest.raises(DeploymentError):dep.submit("load","fixed")
        assert motion.status()["active"]
        motion.stop(result["id"]);wait_done(motion)
        assert motion.status()["phase"]=="completed"
        assert not modes.snapshot().writer_token
    finally:
        if motion.alive():motion.stop(motion.state["id"]);wait_done(motion)


@pytest.mark.parametrize("phase",["loading","ready","paused","running","error"])
def test_model_must_be_released_before_motion(tmp_path,phase):
    _,_,motion=make(tmp_path,phase)
    with pytest.raises(ValueError,match="释放模型"):motion.start("record",name="test")
    assert motion.process is None


def test_trace_listing_rename_no_trace_rewrite_and_symlink(tmp_path):
    _,_,motion=make(tmp_path)
    root=motion.default_root;root.mkdir()
    path=root/"teleop-20261113-test.jsonl"
    path.write_text(json.dumps({"name":"old"})+"\n"+json.dumps({"type":"footer","complete":True,"valid_for_replay":True,"frames":4})+"\n")
    original=path.read_bytes()
    assert motion.records()["records"][0]["valid"]
    assert motion.rename(None,path.name,"新名称")["records"][0]["name"]=="新名称"
    assert path.read_bytes()==original
    with pytest.raises(ValueError):motion.trace(root,"../escape.jsonl")
    (root/"teleop-link.jsonl").symlink_to(path)
    with pytest.raises(ValueError):motion.trace(root,"teleop-link.jsonl")
    assert len(motion.records()["records"])==1


def test_http_read_only_confirmation_and_paths(tmp_path,monkeypatch):
    dep,modes,_=make(tmp_path);app=FastAPI();install_routes(app,dep,modes)
    with TestClient(app) as client:
        assert client.post("/api/motion/action",json={"action":"record","name":"x"}).status_code==409
        assert client.post("/api/motion/action",json={"action":"shell"}).status_code==409
        assert client.get("/api/motion/records",params={"data_root":"/outside"}).status_code==422
        monkeypatch.setenv("COBOT_READ_ONLY","1")
        assert client.post("/api/motion/action",json={"action":"record","confirmed":True,"name":"x"}).status_code==403


def test_dead_registry_does_not_hold_writer_forever(tmp_path):
    _,_,motion=make(tmp_path)
    motion.state={"phase":"stopping","pid":999999999,"start_ticks":5}
    assert not motion.busy() and motion.status()["phase"]=="interrupted"


def test_invalid_names_rejected():
    for value in ["","x\ny","x"*121]:
        with pytest.raises(ValueError):display_name(value)


class Video:
    def __init__(self):self.calls=[];self.current=None
    def start(self,options,identity):self.calls.append("start");self.current=True;return {"state":"recording","directory":"/archive"}
    def finish(self):self.calls.append("finish");self.current=None;return {"state":"error","error":"disk","directory":"/archive"}
    def status(self):return {"state":"recording"}


def test_deployment_video_started_before_movement_and_finalized_without_affecting_result(tmp_path):
    dep,_,_=make(tmp_path);dep.videos=Video();dep.perform("load","fixed")
    original=dep.runtime.action
    def action(name):
        if name=="start":assert dep.videos.calls==["start"]
        return original(name)
    dep.runtime.action=action
    dep.perform("start",video_options={"enabled":True,"data_root":str(tmp_path)})
    dep.perform("pause");dep.perform("resume")
    assert dep.videos.calls==["start"]
    dep.perform("failure")
    record=dep.records()["records"][0]
    assert record["outcome"]=="failure" and record["video"]["error"]=="disk"
    assert dep.runtime.phase=="paused" and dep.active is None


def test_video_failure_before_deployment_start_keeps_model(tmp_path):
    dep,_,_=make(tmp_path);dep.videos=Video();dep.perform("load","fixed")
    def fail(*a):raise RuntimeError("camera offline")
    dep.videos.start=fail
    with pytest.raises(RuntimeError):dep.perform("start",video_options={"enabled":True})
    assert "start" not in dep.runtime.calls and "unload" not in dep.runtime.calls
    assert dep.active is None and dep.runtime.phase=="paused"


def test_video_finish_does_not_block_status(tmp_path):
    archive=VideoArchive(lambda value,create=False:Path(value))
    started=threading.Event();done=threading.Event()
    class Slow:
        root=tmp_path
        def status(self):return {"state":"recording"}
        def stop(self):started.set();done.wait(2);return {"state":"saved"}
    archive.current=Slow()
    thread=threading.Thread(target=archive.finish);thread.start();started.wait(1)
    try:assert archive.status()["state"]=="finalizing"
    finally:done.set();thread.join()


def test_deployment_http_video_options_are_explicit_and_start_only(tmp_path):
    from cobot_console.deployment import install_routes
    dep,modes,_=make(tmp_path);app=FastAPI()
    install_routes(app,dep.cameras,modes,SimpleNamespace(runtime=tmp_path,status=lambda:{"jobs":{}}))
    live=app.state.deployment_manager;calls=[]
    live.submit=lambda *args,**kwargs:calls.append((args,kwargs)) or {"accepted":True}
    with TestClient(app) as client:
        response=client.post("/api/deployment/action",json={"action":"start","video":{"enabled":True,"data_root":str(tmp_path)}})
        assert response.status_code==200 and calls[0][1]["video_options"]=={"enabled":True,"data_root":str(tmp_path)}
        for body in [{"action":"load","video":{"enabled":True}},{"action":"start","video":{"enabled":True,"codec":"jpeg"}},{"action":"start","video":{},"execution_options":{}}]:
            assert client.post("/api/deployment/action",json=body).status_code==422
        assert len(calls)==1


def test_running_hardware_operation_blocks_motion_before_spawn(tmp_path):
    _,_,motion=make(tmp_path)
    motion.hardware_busy=lambda:True
    with pytest.raises(ValueError,match="硬件操作"):motion.start("record",name="test")
    assert motion.process is None
