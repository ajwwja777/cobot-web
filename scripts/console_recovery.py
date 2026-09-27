#!/usr/bin/env python3
"""Cobot terminal recovery: stdlib only, independent of the 8015 web service."""
import argparse
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT
RUNTIME = ROOT / "runtime"
ROLES = ("web", "model", "stage1", "rlt", "arms", "cameras", "home", "recover", "roscore")


class RecoveryError(RuntimeError):
    pass


def read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


# Same configuration as the web service; historical runtime stays in place
# while live tasks are still using it.
HOST_CONFIG = Path(os.environ.get("COBOT_HOST_CONFIG", str(ROOT / "configs/local.json")))
RUNTIME = Path(os.environ.get("COBOT_RUNTIME_ROOT",
    read_json(HOST_CONFIG).get("runtime_root", str(ROOT / "runtime")))).expanduser()

def request(port, path, body=None, timeout=2):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = Request("http://127.0.0.1:%s%s" % (port, path), data=data, headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=timeout) as response:
            return {"ok": True, "status": response.status, "payload": json.load(response)}
    except HTTPError as error:
        try:
            detail = json.load(error)
        except (OSError, ValueError):
            detail = str(error)
        return {"ok": False, "status": error.code, "error": detail}
    except (OSError, ValueError) as error:
        return {"ok": False, "error": str(error)}


def process(pid):
    try:
        directory = Path("/proc") / str(int(pid))
        text = (directory / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        if fields[0] == "Z":
            return None
        return {
            "pid": int(pid), "state": fields[0], "ppid": int(fields[1]),
            "pgid": int(fields[2]), "sid": int(fields[3]), "ticks": int(fields[19]),
            "uid": directory.stat().st_uid,
            "argv": (directory / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1],
        }
    except (OSError, ValueError, IndexError):
        return None


def processes():
    return [value for path in Path("/proc").glob("[0-9]*")
            for value in [process(path.name)] if value]


def registration(role):
    config = read_json(HOST_CONFIG)
    if role == "web":
        try:
            pid = int((RUNTIME / "data-console/service.pid").read_text())
        except (OSError, ValueError):
            return {}
        return {"pid": pid, "web": True}
    if role == "model":
        return read_json(RUNTIME / "deployment/process.json")
    if role == "stage1":
        rlt = config.get("rlt_project_root")
        return read_json(Path(rlt) / "runs/plug_v3_yyshadow/model-server/process.json") if rlt else {}
    if role == "roscore":
        # roscore_up.sh is a short launcher; its job receipt PID is not roscore.
        candidates = [RUNTIME / "roscore/pid"]
        if config.get("legacy_platform_root"):
            candidates.append(Path(config["legacy_platform_root"]) / "runtime/roscore/pid")
        for path in candidates:
            try:
                pid = int(path.read_text())
            except (OSError, ValueError):
                continue
            current = process(pid)
            if current and "/opt/ros/noetic/bin/roscore" in current["argv"]:
                return {"pid": pid, "start_ticks": current["ticks"]}
        return {}
    return read_json(RUNTIME / ("console-jobs/" + role + ".json"))


def targets(role, saved=None):
    """Only registered identities; never substring-scan and kill arbitrary PIDs."""
    saved = registration(role) if saved is None else saved
    pid = saved.get("pid")
    if not isinstance(pid, int) or pid <= 1:
        return []
    leader = process(pid)
    if role == "web":
        if not leader:
            return []
        if leader["uid"] != os.getuid() or "cobot_console.api:app" not in leader["argv"]:
            raise RecoveryError("Web PID no longer belongs to this console")
        try:
            cwd = Path(os.readlink("/proc/%s/cwd" % pid)).resolve()
        except OSError:
            raise RecoveryError("Cannot verify web cwd")
        if cwd != (WEB / "app/backend").resolve():
            raise RecoveryError("Web PID belongs to a different project")
        return [leader]  # Web shares a launcher session: NEVER signal its group.
    ticks = saved.get("start_ticks")
    if not isinstance(ticks, int):
        if leader:
            raise RecoveryError("No recorded start_ticks; inspect this task manually")
        return []
    if leader:
        if leader["ticks"] != ticks or leader["uid"] != os.getuid():
            raise RecoveryError("PID was reused or belongs to another user")
        if leader["pgid"] != pid or leader["sid"] != pid:
            raise RecoveryError("Not an isolated managed session; use its original terminal")
    rows = processes()
    owned = {p["pid"]: p for p in rows if p["sid"] == pid}
    # roslaunch children commonly setsid(). Keep identities observed before our
    # interrupt so those children remain traceable after the launcher exits.
    receipt = read_json(RUNTIME / "recovery-tasks" / (role + ".json"))
    if receipt.get("pid") == pid and receipt.get("start_ticks") == ticks:
        prior = {p["pid"]: p["ticks"] for p in receipt.get("members", [])}
        owned.update({p["pid"]: p for p in rows if prior.get(p["pid"]) == p["ticks"]})
    while True:
        children = {p["pid"]: p for p in rows
                    if p["ppid"] in owned and p["ticks"] >= owned[p["ppid"]]["ticks"]}
        added = set(children) - set(owned)
        owned.update(children)
        if not added:
            break
    members = list(owned.values())
    if any(p["uid"] != os.getuid() or p["ticks"] < ticks for p in members):
        raise RecoveryError("Process tree ownership cannot be verified")
    groups = {p["pgid"] for p in members}
    if any(p["pgid"] in groups and p["pid"] not in owned for p in rows):
        raise RecoveryError("Task shares a process group with unverified processes")
    return members


def remember_targets(role, saved, members):
    """Write only our recovery receipt; never modify the web's task registry."""
    folder = RUNTIME / "recovery-tasks"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (role + ".json")
    temporary = folder / (role + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(json.dumps({
        "pid": saved.get("pid"), "start_ticks": saved.get("start_ticks"),
        "members": [{"pid": p["pid"], "ticks": p["ticks"]} for p in members],
    }, indent=2))
    temporary.replace(path)


def task_rows():
    rows = []
    for role in ROLES:
        saved = registration(role)
        try:
            members, error = targets(role, saved), None
        except RecoveryError as exc:
            members, error = [], str(exc)
        rows.append({"task": role, "registered_pid": saved.get("pid"),
                     "log_path": saved.get("log_path") or saved.get("log"),
                     "members": members, "error": error})
    return rows


def snapshot():
    routes = {"web": (8015, "/api/console/identity"),
              "collection": (8015, "/api/console/status"),
              "model": (8015, "/api/deployment/status"),
              "recorder": (8015, "/api/rlt/recorder-diagnostics"),
              "session": (8026, "/api/session")}
    rows = task_rows()
    tracked = {p["pid"] for row in rows for p in row["members"]}
    markers = {"methods.openpi_rlt.scripts.online_role", "methods.openpi_rlt.plug_v3_yyshadow.serve_stage1",
               "deployment_pi05_client.py", "inference_pi05_rtc_task2.py"}
    unregistered = [p for p in processes() if p["pid"] not in tracked
                    and any(token in markers or Path(token).name in markers for token in p["argv"])]
    return {"time": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "unregistered_model_processes": unregistered,
            "release": read_json(WEB / ".release.json").get("revision"),
            "tasks": rows,
            "services": {name: request(*route) for name, route in routes.items()}}


def print_status(report):
    print("Release:", report["release"], "|", report["time"])
    print("TASK       PID       PGID      SID       STATE  COMMAND")
    for task in report["tasks"]:
        if task["error"]:
            print(task["task"] + ": " + task["error"])
        elif not task["members"]:
            print(task["task"] + ": no live registered process")
        for p in task["members"]:
            print("%-10s %-9s %-9s %-9s %-6s %s" %
                  (task["task"], p["pid"], p["pgid"], p["sid"], p["state"], shlex.join(p["argv"])))
        if task["log_path"]:
            print("  log:", task["log_path"])
    for p in report.get("unregistered_model_processes", []):
        print("UNREGISTERED MODEL (inspect only):",json.dumps(p,ensure_ascii=False))
    for name, result in report["services"].items():
        payload = result.get("payload") or {}
        detail = {k: payload[k] for k in ("phase", "active_mode", "capture_phase", "recorder_state",
                                          "operation", "fault_reason", "policy_paused") if k in payload}
        print(name + ": " + json.dumps({"http": result["status"], **detail} if result["ok"] else result, ensure_ascii=False))


def log_tail(path):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell()-65536))
            return stream.read().decode(errors="replace")
    except OSError as error:
        return str(error)


def save_snapshot(report):
    folder = RUNTIME / "incidents" / (time.strftime("%Y%m%dT%H%M%S") + "-" + str(os.getpid()))
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    (folder / "status.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    for task in report["tasks"]:
        if task["log_path"]:
            (folder / (task["task"] + ".log")).write_text(log_tail(task["log_path"]))
    web_logs = sorted((RUNTIME / "data-console/logs").glob("service-*.log"))
    if web_logs:
        (folder / "web.log").write_text(log_tail(web_logs[-1]))
    print("Evidence saved:", folder)
    return folder


def pause():
    """Pause policy only; never home, terminate, save or resume automatically."""
    reply = request(8026, "/api/session")
    if reply["ok"]:
        state = reply["payload"]
        if state.get("policy_paused") is True:
            print("RLT policy already paused; inspect the physical robot as well.")
            return
        if not all(k in state for k in ("episode_id", "generation")):
            raise RecoveryError("Unexpected Session schema; pause not confirmed")
        token = {k: state[k] for k in ("episode_id", "generation")}
        answer = request(8026, "/api/session/pause", token, timeout=15)
        after = request(8026, "/api/session")
        if not answer["ok"] or not after["ok"] or after["payload"].get("policy_paused") is not True:
            raise RecoveryError("RLT pause not confirmed: " + json.dumps(answer, ensure_ascii=False))
        print("RLT policy pause confirmed.")
        return
    saved = registration("model")
    if saved.get("model", {}).get("kind") != "pi05" or not targets("model", saved):
        raise RecoveryError("No reachable RLT Session or registered pi05 model; pause not confirmed")
    config = read_json(HOST_CONFIG)
    setup = config.get("ros_setup", "/opt/ros/noetic/setup.bash")
    script = WEB / "app/backend/cobot_console/deployment_ros.py"
    command = 'source "$1" && exec /usr/bin/python3 "$2" pause'
    result = subprocess.run(["bash", "-c", command, "cobot-pause", setup, str(script)],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RecoveryError(result.stderr or result.stdout or "pi05 pause failed")
    print(result.stdout.strip())


def interrupt(role, *, execute=False, sig=signal.SIGINT, robot_stopped=False, wait=8):
    saved = registration(role)
    members = targets(role, saved)
    if not members:
        print("No live registered process for", role)
        return []
    print("Target:", role, "| signal:", signal.Signals(sig).name,
          "| PIDs:", [p["pid"] for p in members])
    for p in members:
        print("  PID %s PGID %s SID %s ticks %s %s" %
              (p["pid"],p["pgid"],p["sid"],p["ticks"],shlex.join(p["argv"])))
    if not execute:
        print("Inspection only. Add --execute to send the signal.")
        return members
    if role in {"model","rlt","arms","cameras","home","recover","roscore"} and not robot_stopped:
        raise RecoveryError("Confirm the robot is stopped first, then add --robot-stopped")
    if role == "web" and not robot_stopped:
        state = request(8015, "/api/console/status")
        model = request(8015, "/api/deployment/status")
        if (not state["ok"] or state["payload"].get("active_mode") is not None
                or not model["ok"] or model["payload"].get("active")
                or model["payload"].get("phase") not in {"offline","ready","paused"}):
            raise RecoveryError("Cannot verify idle state. Stop the robot first; emergency override: --robot-stopped")
    if role == "stage1":
        unknown_workers = any("methods.openpi_rlt.scripts.online_role" in p["argv"] for p in processes())
        if targets("model") or targets("rlt") or unknown_workers or request(8026, "/api/session")["ok"]:
            raise RecoveryError("Stop model / RLT workers before releasing Stage 1")
    if role == "roscore" and any(targets(r) for r in ("model","rlt","arms","cameras")):
        raise RecoveryError("Stop model, arms and cameras before ROS Core")
    # Save proof before signalling; do not edit receipts, labels, active records or PID files.
    save_snapshot(snapshot())
    current = targets(role, saved)
    by_pid = {p["pid"]:p for p in current}
    if any(p["pid"] in by_pid and by_pid[p["pid"]]["ticks"] != p["ticks"] for p in members):
        raise RecoveryError("Process changed during inspection")
    if role == "web":
        if not current:
            return []
        if current[0]["ticks"] != members[0]["ticks"]:
            raise RecoveryError("Web PID changed")
        os.kill(current[0]["pid"], sig)
    else:
        remember_targets(role, saved, current)
        for group in sorted({p["pgid"] for p in current}):
            # Re-read before every signal; never signal this operator's own group.
            alive = targets(role, saved)
            if group == os.getpgrp():
                raise RecoveryError("Refusing to signal the operator terminal")
            if any(p["pgid"] == group for p in alive):
                try:
                    os.killpg(group, sig)
                except ProcessLookupError:
                    pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        remaining = targets(role, saved)
        if not remaining:
            print("Exited:", role)
            return []
        time.sleep(.2)
    remaining = targets(role, saved)
    print("Still present:", json.dumps(remaining, ensure_ascii=False))
    print("No stronger signal was sent. Inspect state/logs before another command.")
    return remaining


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("snapshot")
    sub.add_parser("pause")
    logs = sub.add_parser("logs"); logs.add_argument("task", choices=ROLES)
    stop = sub.add_parser("interrupt")
    stop.add_argument("task", choices=ROLES)
    stop.add_argument("--execute", action="store_true")
    stop.add_argument("--robot-stopped", action="store_true",
                      help="Operator confirms the robot is stopped; pending episode may remain incomplete")
    stop.add_argument("--signal", choices=("INT","TERM"), default="INT")
    args = parser.parse_args()
    try:
        if args.command in {"status","snapshot"}:
            report = snapshot(); print_status(report)
            if args.command == "snapshot": save_snapshot(report)
        elif args.command == "pause": pause()
        elif args.command == "logs":
            saved = registration(args.task)
            path = saved.get("log_path") or saved.get("log")
            if args.task == "web":
                files = sorted((RUNTIME/"data-console/logs").glob("service-*.log"))
                path = files[-1] if files else None
            if not path: raise RecoveryError("No registered log file")
            print("Log:",path);print(log_tail(path))
        else:
            remaining = interrupt(args.task,execute=args.execute,
                sig=getattr(signal,"SIG"+args.signal),robot_stopped=args.robot_stopped)
            if args.execute and remaining:return 2
        return 0
    except (RecoveryError, OSError, subprocess.TimeoutExpired) as error:
        print("Not completed:",error,file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
