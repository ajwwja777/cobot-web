"""Bounded loopback access to the internal RLT backend."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

_ALLOWED_PATHS = frozenset(
    (
        "/api/session",
        "/api/session/arm",
        "/api/session/prepare",
        "/api/session/start",
        "/api/session/pause",
        "/api/session/resume",
        "/api/session/stop",
        "/api/episode/next",
        "/api/episode/marker",
        "/api/episode/save",
        "/api/episode/success",
        "/api/episode/failure",
        "/api/episode/abort",
    )
)
_ALLOWED_PHASES = frozenset(
    (
        "offline",
        "loading_machine_a",
        "loading_replay",
        "loading_learner",
        "loading_actor",
        "loading_env",
        "ready_disarmed",
        "ready",
        "stopping",
        "fault",
    )
)


class RltBackendError(RuntimeError):
    """The fixed loopback backend or lifecycle state violated its contract."""


@dataclass(frozen=True)
class BackendResponse:
    status: int
    payload: Dict[str, object]


@dataclass(frozen=True)
class RltLifecycleState:
    generation: Optional[str]
    phase: str
    supervisor_pid: Optional[int]
    step: Optional[int]
    mode: Optional[str]
    updated_at: Optional[str]
    error_code: Optional[str]
    children: Mapping[str, int]


class RltBackendClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8016",
        *,
        timeout_seconds: float = 2.0,
        opener: Optional[Any] = None,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("RLT backend must be loopback HTTP")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = float(timeout_seconds)
        if self._timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._opener = opener or build_opener(ProxyHandler({}))

    def request(
        self, method: str, path: str, body: Optional[Mapping[str, object]] = None
    ) -> BackendResponse:
        method = str(method).upper()
        if method not in {"GET", "POST"} or path not in _ALLOWED_PATHS:
            raise ValueError("invalid_backend_path")
        data = None
        headers = {"Accept": "application/json", "Connection": "close"}
        if body is not None:
            data = json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self._base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=self._timeout_seconds if method == "GET" else max(self._timeout_seconds, 60.0))
            with response:
                return self._decode(response)
        except HTTPError as error:
            try:
                return self._decode(error)
            finally:
                error.close()
        except (OSError, TimeoutError, URLError) as error:
            raise RltBackendError(f"backend_unavailable: {type(error).__name__}") from error

    @staticmethod
    def _decode(response: Any) -> BackendResponse:
        content_type = str(response.headers.get("Content-Type", ""))
        if "application/json" not in content_type.lower():
            raise RltBackendError("backend_not_json")
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RltBackendError("backend_invalid_json") from error
        if not isinstance(payload, dict):
            raise RltBackendError("backend_json_not_object")
        return BackendResponse(status=int(response.status), payload=payload)


class RltLifecycleRegistry:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def read(self) -> RltLifecycleState:
        if not self._path.exists():
            return RltLifecycleState(None, "offline", None, None, None, None, None, {})
        if self._path.is_symlink() or not self._path.is_file():
            raise RltBackendError("invalid_lifecycle_state")
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("state must be an object")
            phase = payload["phase"]
            if phase not in _ALLOWED_PHASES:
                raise ValueError("unknown phase")
            generation = payload.get("generation")
            supervisor_pid = payload.get("supervisor_pid")
            step = payload.get("step")
            mode = payload.get("mode")
            updated_at = payload.get("updated_at")
            error_code = payload.get("error_code")
            children = payload.get("children", {})
            start_ticks = payload.get("supervisor_start_ticks")
            if generation is not None and not isinstance(generation, str):
                raise TypeError("generation")
            if supervisor_pid is not None and (
                isinstance(supervisor_pid, bool) or not isinstance(supervisor_pid, int)
            ):
                raise TypeError("supervisor_pid")
            if step is not None and (isinstance(step, bool) or not isinstance(step, int)):
                raise TypeError("step")
            if mode is not None and not isinstance(mode, str):
                raise TypeError("mode")
            if updated_at is not None and not isinstance(updated_at, str):
                raise TypeError("updated_at")
            if error_code is not None and not isinstance(error_code, str):
                raise TypeError("error_code")
            if not isinstance(children, dict) or any(
                not isinstance(name, str)
                or isinstance(pid, bool)
                or not isinstance(pid, int)
                for name, pid in children.items()
            ):
                raise TypeError("children")
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RltBackendError("invalid_lifecycle_state") from error
        if phase not in {"offline", "fault"}:
            try:
                if not isinstance(start_ticks, int) or not isinstance(supervisor_pid, int):
                    raise ValueError("missing process identity")
                text = (Path("/proc") / str(supervisor_pid) / "stat").read_text(encoding="utf-8")
                fields = text[text.rfind(")") + 2:].split()
                if fields[0] == "Z" or int(fields[19]) != start_ticks:
                    raise ValueError("process identity changed")
            except (OSError, ValueError, IndexError):
                phase = "fault"
                error_code = "supervisor_unavailable"
        return RltLifecycleState(
            generation,
            phase,
            supervisor_pid,
            step,
            mode,
            updated_at,
            error_code,
            dict(children),
        )
