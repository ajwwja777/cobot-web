"""Ordinary DAgger collection uses the shared inference process, not evaluations."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel
from typing import Optional, Dict

from .deployment import DeploymentError


class CollectionModelAction(BaseModel):
    action: str
    model_id: str = ""
    execution_options: Optional[Dict[str, object]] = None


class CollectionModel:
    def __init__(self, manager):
        self.manager = manager
        self.episode_model = None
        self.episode_uuid = None
        self.online = False

    def before_start(self, use_model=False, model_id=None):
        manager = self.manager
        if self.episode_uuid:
            raise DeploymentError("上一轮 EXPO 在线收尾尚未确认，请先重试结束")
        with manager.lock:
            if manager.operation or manager.active:
                raise DeploymentError("请先结束部署操作或评估轮次")
            state = manager.runtime.status()
            if use_model:
                if (state.get("model", {}).get("kind") not in {"pi05", "vla"}
                        or state.get("model", {}).get("id") != model_id
                        or state.get("phase") not in {"ready", "paused"}):
                    raise DeploymentError("请先加载所选采集模型并等待就绪")
                if state["model"].get("runtime_adapter") == "expo_online" and (state.get("online") or {}).get("phase") != "ready":
                    raise DeploymentError("EXPO 正在更新或尚未就绪，请等待在线状态就绪")
                manager.collection_session = True
                self.episode_model = model_id
                self.online = state["model"].get("runtime_adapter") == "expo_online"
            else:
                if state.get("phase") == "running":
                    raise DeploymentError("请先暂停正在推理的模型")
                self.episode_model = None
                self.online = False

    def online_request(self, path, body=None):
        import json
        from urllib.request import Request, ProxyHandler, build_opener
        from urllib.error import HTTPError, URLError
        payload={"model_id":self.episode_model,"episode_uuid":self.episode_uuid,**(body or {})}
        request=Request("http://127.0.0.1:8044"+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        try:
            with build_opener(ProxyHandler({})).open(request,timeout=20) as response:return json.load(response)
        except HTTPError as error:
            try:detail=json.load(error).get("error",str(error))
            except Exception:detail=str(error)
            raise DeploymentError("EXPO 在线任务："+detail) from error
        except (URLError,OSError) as error:
            raise DeploymentError("EXPO 在线服务未响应："+str(error)) from error

    def started(self, capture):
        if self.online:
            identity=str(capture["episode_uuid"])
            self.episode_uuid=identity
            self.online_request("/episode/start",{"episode_uuid":identity})

    def finalized(self, outcome, identity):
        if self.online and self.episode_uuid:
            if str(identity)!=self.episode_uuid:raise DeploymentError("EXPO 回合身份已变化")
            self.online_request("/episode/end",{"outcome":outcome or "unknown"})
            self.episode_uuid=None

    def action(self, name):
        if not self.episode_model:
            return
        if name == "marker":
            return
        state = self.manager.runtime.status()
        if state.get("model", {}).get("id") != self.episode_model:
            raise DeploymentError("采集模型已变化")
        if name in {"stop", "discard", "pause"} and state.get("phase") == "offline":
            return  # A stopped process cannot issue further commands.
        self.manager.runtime.action("resume" if name in {"start", "resume"} else "pause")
        if self.online and self.episode_uuid:
            self.online_request("/episode/resume" if name in {"start","resume"} else "/episode/pause")
        self.manager.refresh()

    def finished(self):
        if self.online and self.episode_uuid:
            self.finalized("aborted",self.episode_uuid)
        self.episode_model = None
        self.episode_uuid = None
        self.online = False

    def status(self):
        state = self.manager.status()
        return {"enabled": self.episode_model is not None,
                "running": bool(self.episode_model and state.get("phase") == "running"),
                "session_active": getattr(self.manager, "collection_session", False)}


def install_routes(app, manager):
    from .rl_monitoring import install_routes as install_monitoring
    install_monitoring(app, manager)
    controller = CollectionModel(manager)
    app.state.collection_model = controller
    manager.collection_session = False

    @app.get("/api/collection/model")
    def status():
        return manager.status()

    @app.post("/api/collection/model")
    def action(request: CollectionModelAction):
        try:
            if request.action not in {"load", "unload", "session_start", "session_stop"}:
                raise HTTPException(422, "unknown_collection_action")
            name = {"session_start": "collection_session_start", "session_stop": "collection_session_stop"}.get(request.action, request.action)
            if request.execution_options is not None:
                if name != 'load':
                    raise HTTPException(422, 'execution_options_only_on_load')
                from .execution_options import contract
                try:
                    options = contract()['normalize_options'](request.execution_options)
                except (ValueError, TypeError) as error:
                    raise HTTPException(422, str(error)) from error
                return manager.submit(name, request.model_id or None, execution_options=options, load_use='collection')
            return manager.submit(name, request.model_id or None, **({'load_use':'collection'} if name=='load' else {}))
        except DeploymentError as error:
            raise HTTPException(409, str(error)) from error
