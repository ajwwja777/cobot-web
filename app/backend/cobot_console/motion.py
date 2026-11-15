"""Web orchestration of the control CLI and optional independent camera archives."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from uuid import uuid4
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .paths import CONTROL, DATA, RUNTIME_ROOT
from .deployment import atomic_json, read_json, process_identity, tail, DeploymentError
from capture_core.storage import _reject_symlink_components
from capture_core.asset_storage import require_storage


class VideoOptions(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: bool=False
    data_root: Optional[str]=None


def display_name(value):
    value=str(value).strip()
    if not value or len(value)>120 or any(ord(ch)<32 for ch in value):
        raise ValueError("请输入 1–120 字符的轨迹名称")
    return value


class VideoArchive:
    def __init__(self, resolve):
        self.resolve=resolve;self.current=None;self.last=None;self.finalizing=False;self.lock=threading.RLock()

    def start(self, options, identity):
        options=VideoOptions(**(options or {}))
        if not options.enabled:
            with self.lock:self.last=None
            return None
        with self.lock:
            if self.current or self.finalizing:raise ValueError("已有原视频录制")
            root=self.resolve(options.data_root or str(DATA/"videos/deployment"),create=True)
            folder=root/(identity+"-"+uuid4().hex[:8])
            from capture_core.uncompressed_video import ThreeCameraVideo
            video=ThreeCameraVideo(folder,metadata={"run_id":identity})
            self.current=video
        try:video.start()
        except Exception:
            self.finish();raise
        return self.status()

    def status(self):
        with self.lock:return self.current.status() if self.current else self.last

    def finish(self):
        with self.lock:
            if not self.current:return self.last
            video=self.current;self.current=None;self.finalizing=True
            self.last={**video.status(),"state":"finalizing"}
        try:result=video.stop()
        except Exception as exc:result={"state":"error","directory":str(video.root),"error":str(exc)}
        with self.lock:self.last=result;self.finalizing=False
        return result


class MotionManager:
    def __init__(self, deployment, modes, resolve, *, directory=RUNTIME_ROOT/"motion", popen=subprocess.Popen, hardware_busy=lambda:False):
        self.deployment=deployment;self.modes=modes;self.resolve=resolve
        self.directory=Path(directory);self.registry=self.directory/"process.json"
        self.hardware_busy=hardware_busy
        self.launching=False;self.popen=popen;self.process=None;self.lease=None;self.videos=VideoArchive(resolve)
        self.state=read_json(self.registry);self.lock=threading.RLock()
        self.default_root=Path(os.environ.get("COBOT_TELEOP_DATA_ROOT",str(DATA/"motion/replays")))

    def alive(self):
        return bool(self.state.get("pid") and self.state.get("start_ticks") is not None and process_identity(self.state["pid"])==self.state.get("start_ticks"))

    def busy(self):return self.alive() or self.launching

    def status(self):
        with self.lock:
            active=self.alive()
            phase=self.state.get("phase","idle")
            if not active and not self.launching and phase in {"recording","replaying","starting","stopping"}:phase="interrupted"
            return {**self.state,"phase":phase,"active":active or self.launching,
                    "log_tail":tail(self.state.get("log_path", "")),"default_data_root":str(self.default_root),
                    "video":self.videos.status()}

    def trace(self, root, name):
        root=self.resolve(str(root))
        if not re.fullmatch(r"teleop-[A-Za-z0-9_-]+[.]jsonl",name):raise ValueError("invalid_trace")
        path=root/name;_reject_symlink_components(path)
        if not path.is_file() or path.resolve().parent!=root:raise ValueError("trace_not_found")
        return path

    def records(self, data_root=None):
        root=self.resolve(data_root or str(self.default_root))
        rows=[]
        if root.is_dir():
            for path in sorted(root.glob("teleop-*.jsonl"),reverse=True):
                if path.is_symlink():continue
                try:
                    with path.open("rb") as stream:
                        header=json.loads(stream.readline())
                        stream.seek(max(0,path.stat().st_size-4096));lines=stream.read().splitlines();footer=json.loads(lines[-1])
                    renamed=read_json(path.with_suffix(".name.json")).get("name")
                    rows.append({"id":path.name,"name":renamed or header.get("name") or path.stem,
                                 "frames":footer.get("frames",0),"duration_sec":footer.get("duration_sec"),
                                 "valid":footer.get("complete") is True and footer.get("valid_for_replay") is True,
                                 "error":footer.get("error"),"path":str(path)})
                except (OSError,ValueError,IndexError):
                    rows.append({"id":path.name,"name":path.stem,"valid":False,"error":"invalid_trace"})
        return {"data_root":str(root),"records":rows}

    def rename(self, root, trace_id, name):
        with self.deployment.lock,self.lock:
            if self.busy():raise ValueError("请先结束轨迹任务")
            path=self.trace(root or self.default_root,trace_id)
            atomic_json(path.with_suffix(".name.json"),{"name":display_name(name)})
            return self.records(root)

    def start(self, action, *, name="", trace_id="", data_root=None, video=None):
        with self.deployment.lock,self.lock:
            if self.busy():raise ValueError("已有轨迹任务；请先停止")
            state=self.deployment.status()
            actual=self.deployment.runtime.status()
            if self.hardware_busy():raise ValueError("请先等待当前硬件操作结束")
            if self.deployment.operation or self.deployment.active or self.deployment.busy() or self.deployment.collection_session:
                raise ValueError("请先结束当前采集或部署任务")
            if actual.get("phase")!="offline":raise ValueError("请先释放模型，再录制或回放轨迹")
            root=self.resolve(data_root or str(self.default_root),create=action=="record")
            args=[str(CONTROL/"scripts/teleop.sh"),action]
            if action=="record":args.extend(["--name",display_name(name)])
            else:args.append(str(self.trace(root,trace_id)))
            args.append("--web-confirmed")
            lease=self.modes.acquire_writer(self.modes.snapshot().selected_mode)
            self.lease=lease
            run_id="motion-"+uuid4().hex
            self.directory.mkdir(parents=True,exist_ok=True)
            log_path=self.directory/(run_id+".log")
            self.state={"id":run_id,"action":action,"phase":"starting","data_root":str(root),
                        "trace_id":trace_id,"name":name,"started_at":time.time(),"log_path":str(log_path)}
            self.launching=True
            try:
                self.videos.start(video,run_id)
                env=dict(os.environ,COBOT_TELEOP_DATA_ROOT=str(root))
                with log_path.open("xb") as log:
                    self.process=self.popen(args,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,
                                            env=env,start_new_session=True)
                self.state.update(pid=self.process.pid,start_ticks=process_identity(self.process.pid),
                                  phase="recording" if action=="record" else "replaying",command=args)
                if self.state["start_ticks"] is None and self.process.poll() is None:raise ValueError("cannot verify motion process")
                atomic_json(self.registry,self.state)
                threading.Thread(target=self._wait,args=(self.process,run_id,lease),daemon=True,name="motion-result").start()
            except Exception:
                if self.process and self.process.poll() is None:
                    try:
                        os.killpg(self.process.pid,signal.SIGINT)
                        self.process.wait(timeout=5)
                    except (OSError,subprocess.TimeoutExpired):pass
                self.videos.finish();self.modes.release_writer(lease);self.lease=None
                self.state["phase"]="error";atomic_json(self.registry,self.state);raise
            finally:self.launching=False
            return self.status()

    def _wait(self, process, identity, lease):
        code=process.wait()
        with self.deployment.lock,self.lock:
            if self.state.get("id")!=identity:return
            self.state.update(phase="completed" if code==0 else "interrupted" if code==130 else "error",exit_code=code,ended_at=time.time())
            self.state["video"]=self.videos.finish()
            atomic_json(self.registry,self.state)
            self.modes.release_writer(lease);self.lease=None
            if process.stdin:process.stdin.close()

    def stop(self, identity):
        with self.lock:
            if self.state.get("id")!=identity:raise ValueError("轨迹任务已变化，请刷新")
            if not self.alive():return self.status()
            self.state["phase"]="stopping";atomic_json(self.registry,self.state)
            try:
                if self.state.get("action")=="record" and self.process and self.process.stdin:
                    self.process.stdin.write(b"stop\n");self.process.stdin.flush()
                elif os.getpgid(self.state["pid"])==self.state["pid"]:
                    # Only this verified, isolated CLI session and its setup children.
                    os.killpg(self.state["pid"],signal.SIGINT)
                else:raise ValueError("轨迹进程组身份不匹配")
            except (ProcessLookupError,BrokenPipeError):pass
            return self.status()

    def shutdown(self):
        if self.alive():self.stop(self.state["id"])
        self.videos.finish()


class MotionAction(BaseModel):
    model_config=ConfigDict(extra="forbid")
    action: str
    name: str=""
    trace_id: str=""
    data_root: Optional[str]=None
    run_id: str=""
    video: Optional[VideoOptions]=None
    confirmed: bool=False


def install_routes(app, manager, modes, devices=None):
    def resolve(value, create=False):
        candidate=Path(value).expanduser()
        if not candidate.is_absolute():raise ValueError("请选择绝对保存路径")
        _reject_symlink_components(candidate)
        resolver=getattr(app.state,"resolve_data_root",None)
        root=resolver(str(candidate)) if resolver else candidate.resolve()
        if resolver is None and manager.allowed_root!=root and manager.allowed_root not in root.parents:
            raise ValueError("data_root_not_allowed")
        require_storage(root,write=create)
        if create:root.mkdir(parents=True,exist_ok=True)
        return root
    def hardware_busy():
        if devices is None:return False
        jobs=devices.status().get("jobs",{})
        return any(jobs.get(key,{}).get("phase") in {"starting","running","stopping"}
                   for key in ("can","home","recover","pose","front_reset","front_mode"))
    motion=MotionManager(manager,modes,resolve,hardware_busy=hardware_busy);app.state.motion_manager=motion
    manager.motion=motion;manager.videos=VideoArchive(resolve)
    from .video_downloads import install_routes as install_video_downloads
    install_video_downloads(app,resolve,motion,manager)

    @app.get("/api/motion/status")
    def status():return motion.status()

    @app.get("/api/motion/records")
    def records(data_root: Optional[str]=None):
        try:return motion.records(data_root)
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc)) from exc

    @app.post("/api/motion/action")
    def action(request:MotionAction):
        if os.environ.get("COBOT_READ_ONLY")=="1":raise HTTPException(403,"read_only_preview")
        try:
            if request.action in {"record","replay"}:
                if not request.confirmed:raise ValueError("请明确确认开始轨迹操作")
                return motion.start(request.action,name=request.name,trace_id=request.trace_id,data_root=request.data_root,
                                    video=request.video.model_dump() if request.video else None)
            if request.action=="stop":return motion.stop(request.run_id)
            if request.action=="rename":return motion.rename(request.data_root,request.trace_id,request.name)
            raise ValueError("unknown_motion_action")
        except (ValueError,OSError,DeploymentError,RuntimeError) as exc:raise HTTPException(409,str(exc)) from exc
