"""Ordinary DAgger collection uses the shared inference process, not evaluations."""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel

from .deployment import DeploymentError


class CollectionModelAction(BaseModel):
    action: str
    model_id: str = ""


class CollectionModel:
    def __init__(self, manager):
        self.manager = manager
        self.episode_model = None

    def before_start(self, use_model=False, model_id=None):
        manager = self.manager
        with manager.lock:
            if manager.operation or manager.active:
                raise DeploymentError("请先结束部署操作或评估轮次")
            state = manager.runtime.status()
            if use_model:
                if (state.get("model", {}).get("kind") != "pi05"
                        or state.get("model", {}).get("id") != model_id
                        or state.get("phase") not in {"ready", "paused"}):
                    raise DeploymentError("请先加载所选采集模型并等待就绪")
                manager.collection_session = True
                self.episode_model = model_id
            else:
                if state.get("phase") == "running":
                    raise DeploymentError("请先暂停正在推理的模型")
                self.episode_model = None

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
        self.manager.refresh()

    def finished(self):
        self.episode_model = None

    def status(self):
        state = self.manager.status()
        return {"enabled": self.episode_model is not None,
                "running": bool(self.episode_model and state.get("phase") == "running"),
                "session_active": getattr(self.manager, "collection_session", False)}


def install_routes(app, manager):
    controller = CollectionModel(manager)
    app.state.collection_model = controller
    manager.collection_session = False

    @app.get("/api/collection/model")
    def status():
        return {**manager.status(), "session_active": manager.collection_session}

    @app.post("/api/collection/model")
    def action(request: CollectionModelAction):
        try:
            if request.action not in {"load", "unload", "session_start", "session_stop"}:
                raise HTTPException(422, "unknown_collection_action")
            if request.action == "load":
                model = next((m for m in manager.models if m["id"] == request.model_id), {})
                if model.get("kind") != "pi05":
                    raise DeploymentError("普通采集请选择 π0.5 / DAgger 模型")
            name = {"session_start": "collection_session_start", "session_stop": "collection_session_stop"}.get(request.action, request.action)
            return manager.submit(name, request.model_id or None)
        except DeploymentError as error:
            raise HTTPException(409, str(error)) from error
