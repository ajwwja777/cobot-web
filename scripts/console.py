#!/usr/bin/env python3
"""Terminal client for the same Cobot API used by the browser. No automatic retries."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
STATES = {
    "console": "/api/console/status", "devices": "/api/console/devices",
    "cameras": "/api/console/cameras", "capture": "/api/segmented-teach/status",
    "session": "/api/rlt/session", "model": "/api/deployment/status",
    "outputs": "/api/console/outputs", "training": "/api/console/diagnostics",
    "recorder": "/api/rlt/recorder-diagnostics", "storage": "/api/rlt/storage",
    "records": "/api/deployment/records", "host": "/api/console/host",
}


class ConsoleError(RuntimeError):
    pass


class Client:
    def __init__(self, url="http://127.0.0.1:8015", timeout=15):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "10.7.165.64"}:
            raise ConsoleError("Use the registered Cobot host or an SSH localhost tunnel")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConsoleError("Credentials/query are not allowed in the service URL")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}))

    def request(self, method, path, body=None, output=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ConsoleError("API path must start with a single /")
        headers = {"Accept": "application/json"}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = Request(self.url + path, method=method, headers=headers, data=raw)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read()
                if output:
                    Path(output).write_bytes(raw)
                    return {"saved": str(output), "bytes": len(raw), "http": response.status}
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            detail = error.read().decode(errors="replace")[:4000]
            raise ConsoleError("HTTP %s %s %s\n%s\n%s" %
                (error.code, method, path, detail, advice(error.code, method))) from error
        except (OSError, URLError, ValueError) as error:
            raise ConsoleError("%s %s: %s\n%s" %
                (method, path, error, advice(None, method))) from error


def advice(status, method):
    checks = "先查 state console、state model 和对应任务日志。"
    if status in (409, 422):
        checks = "检查返回字段、当前 Episode/Session 和参数；不要通过重启跳过状态检查。"
    elif status in (500, 503):
        checks = "检查后端、录制器和日志；503 也可能是接口错误，重启不一定解决。"
    elif status is None:
        checks = "连接/读取未完成。检查 8015 服务；必要时用 recovery status / snapshot。"
    if method not in ("GET", "HEAD", "OPTIONS"):
        checks += " 请求结果待确认，不要重复开始、保存或判定结果。"
    return checks + " 见 docs/WEB_RECOVERY.md。"


def version(state, rlt=False):
    keys = ("episode_id", "generation") if rlt else ("episode_uuid", "generation")
    if any(state.get(key) is None for key in keys):
        raise ConsoleError("No active episode identity; query current status first")
    return {key: state[key] for key in keys}


def device(client, args):
    operation = {"component": args.component, "action": args.action}
    for key in ("target", "pose", "model"):
        value = getattr(args, key, None)
        if value:
            operation[key] = value
    if args.arms:
        operation["arms"] = args.arms.split(",")
    if args.component == "can":
        operation["target"] = "task2"  # Existing CAN profile identifier, not a project name.
    confirmed = client.request("POST", "/api/console/devices/confirm", operation)
    if args.component == "can":
        operation["sudo_password"] = getpass.getpass("Cobot sudo password (not recorded): ")
    return client.request("POST", "/api/console/devices/action", {
        "operation": operation, "confirmation_token": confirmed["confirmation_token"]})


def wait_model(client, seconds):
    deadline = time.monotonic() + seconds
    while True:
        state = client.request("GET", "/api/deployment/status")
        if (state.get("error") or state.get("phase") in {"error", "fault"}
                or (state.get("phase") == "offline" and not state.get("operation"))):
            raise ConsoleError("Model not ready: " + json.dumps(state, ensure_ascii=False))
        if not state.get("operation") and state.get("phase") in {"ready", "paused", "running"}:
            return state
        if time.monotonic() >= deadline:
            raise ConsoleError("Wait ended; model may still be loading. Check state model and recovery logs model.")
        time.sleep(1)


def model(client, args):
    if args.action == "wait":
        return wait_model(client, args.seconds)
    if args.action in {"list", "status"}:
        state = client.request("GET", "/api/deployment/status")
        return state.get("models", []) if args.action == "list" else state
    if args.action == "load" and not args.id:
        raise ConsoleError("model load requires --id from model list")
    if args.action == "session-start":
        state = client.request("GET", "/api/deployment/status")
        kind = state.get("model", {}).get("kind")
        if state.get("phase") not in {"ready", "paused"} or not kind:
            raise ConsoleError("Load the model and wait for ready/paused first")
        client.request("POST", "/api/console/mode", {"mode": "rlt" if kind == "rlt" else "normal"})
    return client.request("POST", "/api/collection/model", {
        "action": args.action.replace("-", "_"), "model_id": args.id or ""})


def start_body(args):
    if not args.data_root:
        raise ConsoleError("--data-root is required for ordinary capture")
    return {"data_root": args.data_root, "task_id": args.task, "model_id": args.dataset_model,
            "checkpoint_id": args.checkpoint, "dataset_round": args.round, "storage_layout": "flat",
            "use_model": bool(args.model), "collection_model_id": args.model}


def capture(client, args):
    if args.action == "start":
        loaded = client.request("GET", "/api/deployment/status") if args.model else {}
        if args.model and (loaded.get("model", {}).get("id") != args.model
                           or loaded.get("phase") not in {"ready", "paused"}):
            raise ConsoleError("Load --model first and wait for ready/paused")
        rlt = bool(args.model and loaded.get("model", {}).get("kind") == "rlt")
        body = None if rlt else start_body(args)
        client.request("POST", "/api/console/mode", {"mode": "rlt" if rlt else "normal"})
        if not rlt:
            return client.request("POST", "/api/segmented-teach/start", body)
        if args.data_root:
            client.request("POST", "/api/rlt/storage", {"data_root": args.data_root})
        state = client.request("GET", "/api/rlt/session")
        if state.get("phase") in {"disarmed", "stopped"}:
            raise ConsoleError("Run model session-start first; no inference was started")
        action = "episode/next" if state.get("phase") == "waiting_scene" else "session/start"
        return client.request("POST", "/api/rlt/" + action, version(state, True))
    mode = client.request("GET", "/api/console/status")
    rlt = (mode.get("active_mode") or mode.get("selected_mode")) == "rlt"
    if rlt:
        paths = {"pause": "session/pause", "resume": "session/resume", "marker": "episode/marker",
                 "save": "episode/save", "discard": "episode/abort",
                 "success": "episode/success", "failure": "episode/failure"}
        state = client.request("GET", "/api/rlt/session")
        body = version(state, True)
        if args.action in {"save", "discard", "success", "failure"}:
            body["home_after_terminal"] = False  # Explicit home command selects actual arms/pose.
        return client.request("POST", "/api/rlt/" + paths[args.action], body)
    state = client.request("GET", "/api/segmented-teach/status")
    body = version(state)
    action = args.action
    if action in {"save", "success", "failure"}:
        body["outcome"] = "unknown" if action == "save" else action
        action = "stop"
    return client.request("POST", "/api/segmented-teach/" + action, body)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8015")
    parser.add_argument("--timeout", type=float, default=15)
    sub = parser.add_subparsers(dest="command", required=True)
    state = sub.add_parser("state", help="Read browser/backend state")
    state.add_argument("area", choices=sorted(STATES))
    api = sub.add_parser("api", help="Access every registered API, including advanced operations")
    api.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    api.add_argument("path")
    data = api.add_mutually_exclusive_group()
    data.add_argument("--json"); data.add_argument("--file")
    api.add_argument("--output", help="Save a binary response or schema to this local file")
    routes = sub.add_parser("routes", help="List exact routes from the running service's OpenAPI")
    routes.add_argument("--recorder", action="store_true")
    hardware = sub.add_parser("device", help="Run the same confirm/action protocol as web buttons")
    hardware.add_argument("component", choices=["can", "roscore", "arms", "cameras", "home", "recover", "pose", "rlt"])
    hardware.add_argument("action")
    for name in ("target", "pose", "arms", "model"):
        hardware.add_argument("--" + name)
    models = sub.add_parser("model")
    models.add_argument("action", choices=["list", "status", "load", "unload", "wait", "session-start", "session-stop"])
    models.add_argument("--id"); models.add_argument("--seconds", type=float, default=600)
    recording = sub.add_parser("capture")
    recording.add_argument("action", choices=["start", "pause", "resume", "marker", "save", "discard", "success", "failure"])
    recording.add_argument("--data-root"); recording.add_argument("--model")
    recording.add_argument("--task", default="plug"); recording.add_argument("--dataset-model", default="expert")
    recording.add_argument("--checkpoint", default="manual"); recording.add_argument("--round", default="collection")
    storage = sub.add_parser("storage")
    storage.add_argument("kind", choices=["normal", "rlt", "evaluation"])
    storage.add_argument("path")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("action", choices=["start", "pause", "resume", "success", "failure", "abort"])
    evaluate.add_argument("--model")
    sub.add_parser("recovery", help="No-web status/logs/pause/identity-checked interrupt").add_argument("args", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "recovery":
            return subprocess.call([sys.executable, str(ROOT / "scripts/console_recovery.py")] + args.args)
        if args.timeout <= 0:
            raise ConsoleError("--timeout must be positive")
        client = Client(args.url, args.timeout)
        if args.command == "state":
            result = client.request("GET", STATES[args.area])
        elif args.command == "api":
            body = json.loads(Path(args.file).read_text() if args.file else args.json) if args.file or args.json else None
            result = client.request(args.method, args.path, body, args.output)
        elif args.command == "routes":
            schema = client.request("GET", "/api/rlt-recorder/openapi.json" if args.recorder else "/openapi.json")
            result = [{"method": method.upper(), "path": path, "schema": route.get("requestBody")}
                      for path, item in schema["paths"].items()
                      for method, route in item.items() if method in {"get", "post", "put", "patch", "delete"}]
        elif args.command == "device":
            result = device(client, args)
        elif args.command == "model":
            result = model(client, args)
        elif args.command == "capture":
            result = capture(client, args)
        elif args.command == "storage":
            path = {"normal": "/api/segmented-teach/storage/prepare", "rlt": "/api/rlt/storage",
                    "evaluation": "/api/deployment/storage"}[args.kind]
            body = {"data_root": args.path}
            if args.kind == "normal":
                body["storage_layout"] = "flat"
            result = client.request("POST", path, body)
        elif args.command == "evaluate":
            state = client.request("GET", "/api/deployment/status")
            result = client.request("POST", "/api/deployment/action", {
                "action": args.action, "model_id": args.model or (state.get("model") or {}).get("id"),
                "trial_id": (state.get("active") or {}).get("id")})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ConsoleError, OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
