import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

spec=importlib.util.spec_from_file_location("recovery",Path(__file__).parents[3]/"scripts/console_recovery.py")
recovery=importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture(autouse=True)
def isolated_recovery_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "RUNTIME", tmp_path / "runtime")


def test_pid_reuse_and_shared_terminal_session_are_rejected(monkeypatch):
    saved={"pid":500000,"start_ticks":42}
    current={"pid":500000,"uid":os.getuid(),"ticks":99,"sid":500000,"pgid":500000}
    monkeypatch.setattr(recovery,"process",lambda pid:current)
    with pytest.raises(recovery.RecoveryError,match="reused"):recovery.targets("model",saved)
    current.update(ticks=42,sid=10,pgid=10)
    with pytest.raises(recovery.RecoveryError,match="original terminal"):recovery.targets("model",saved)


def test_no_recorded_identity_is_not_silently_trusted(monkeypatch):
    monkeypatch.setattr(recovery,"process",lambda pid:{"pid":pid})
    with pytest.raises(recovery.RecoveryError,match="start_ticks"):
        recovery.targets("arms",{"pid":500000})


@pytest.mark.parametrize("orphan",[False,True])
@pytest.mark.parametrize("new_session", [False, True])
def test_interrupt_handles_children_in_separate_groups_and_dead_leader(tmp_path,monkeypatch,orphan,new_session):
    target=tmp_path/"child.txt"
    child_code="import os,time;" + ("os.setsid();" if new_session else "os.setpgrp();") + "time.sleep(60)"
    code=("import subprocess,sys,time;from pathlib import Path;"
          "p=subprocess.Popen([sys.executable,'-c',"+repr(child_code)+"]);"
          "Path("+repr(str(target))+").write_text(str(p.pid));time.sleep("+(".5" if orphan else "60")+")")
    parent=subprocess.Popen([sys.executable,"-c",code],start_new_session=True,
                            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    saved={"pid":parent.pid,"start_ticks":recovery.process(parent.pid)["ticks"]}
    child=None
    try:
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if target.exists():
                child=int(target.read_text())
                row=recovery.process(child)
                if row and row["pgid"]==child:break
            time.sleep(.01)
        assert child and recovery.process(child)["sid"] == (child if new_session else parent.pid)
        if orphan and new_session:
            recovery.remember_targets("model", saved, recovery.targets("model", saved))
        if orphan:parent.wait(timeout=3)
        monkeypatch.setattr(recovery,"registration",lambda role:saved)
        monkeypatch.setattr(recovery,"snapshot",lambda:{})
        monkeypatch.setattr(recovery,"save_snapshot",lambda report:None)
        expected={child} if orphan else {parent.pid,child}
        assert {p["pid"] for p in recovery.interrupt("model")} == expected
        assert recovery.process(child)  # Default is inspection only.
        with pytest.raises(recovery.RecoveryError,match="robot"):
            recovery.interrupt("model",execute=True)
        assert recovery.process(child)
        assert recovery.interrupt("model",execute=True,robot_stopped=True,wait=3) == []
        parent.wait(timeout=3)
        assert not recovery.process(child)
    finally:
        for group in [parent.pid,child]:
            if group:
                try:os.killpg(group,signal.SIGTERM)
                except ProcessLookupError:pass
        parent.wait(timeout=3)


def test_web_interrupt_signals_only_its_pid(monkeypatch):
    row={"pid":500000,"pgid":70,"sid":70,"ticks":2,"argv":["uvicorn"],"state":"S"}
    live=[row];signals=[]
    monkeypatch.setattr(recovery,"registration",lambda role:{"pid":row["pid"]})
    monkeypatch.setattr(recovery,"targets",lambda *args:list(live))
    monkeypatch.setattr(recovery,"snapshot",lambda:{})
    monkeypatch.setattr(recovery,"save_snapshot",lambda report:None)
    def signal_pid(pid,sig):signals.append((pid,sig));live.clear()
    monkeypatch.setattr(os,"kill",signal_pid)
    monkeypatch.setattr(os,"killpg",lambda *args:pytest.fail("web group must never be signalled"))
    assert recovery.interrupt("web",execute=True,robot_stopped=True,wait=.1) == []
    assert signals==[(500000,signal.SIGINT)]


def test_pause_uses_8026_and_requires_confirmation(monkeypatch):
    replies=iter([
        {"ok":True,"payload":{"policy_paused":False,"episode_id":7,"generation":9}},
        {"ok":True,"payload":{}},
        {"ok":True,"payload":{"policy_paused":True}}])
    calls=[]
    def request(port,path,body=None,**kwargs):
        calls.append((port,path,body));return next(replies)
    monkeypatch.setattr(recovery,"request",request)
    recovery.pause()
    assert calls==[(8026,"/api/session",None),(8026,"/api/session/pause",{"episode_id":7,"generation":9}),(8026,"/api/session",None)]


def test_no_api_and_no_known_pi05_does_not_claim_pause(monkeypatch):
    monkeypatch.setattr(recovery,"request",lambda *args,**kwargs:{"ok":False,"error":"offline"})
    monkeypatch.setattr(recovery,"registration",lambda *args:{})
    with pytest.raises(recovery.RecoveryError,match="not confirmed"):recovery.pause()


def test_unexpected_session_schema_does_not_send_pause(monkeypatch):
    monkeypatch.setattr(recovery, "request", lambda *args, **kwargs: {"ok": True, "payload": {}})
    with pytest.raises(recovery.RecoveryError, match="schema"):
        recovery.pause()


def test_stage1_is_not_stopped_while_workers_are_alive(monkeypatch):
    row = {"pid": 500000, "pgid": 500000, "sid": 500000, "ticks": 2, "argv": ["stage1"]}
    monkeypatch.setattr(recovery, "registration", lambda role: {})
    monkeypatch.setattr(recovery, "targets", lambda role, *args: [row] if role in {"stage1", "model"} else [])
    monkeypatch.setattr(recovery, "processes", lambda: [])
    monkeypatch.setattr(os, "killpg", lambda *args: pytest.fail("Stage 1 must not be signalled"))
    with pytest.raises(recovery.RecoveryError, match="workers"):
        recovery.interrupt("stage1", execute=True)


def test_pause_response_without_paused_state_is_not_success(monkeypatch):
    monkeypatch.setattr(recovery, "request", lambda *args, **kwargs: {
        "ok": True, "payload": {"episode_id": 7, "generation": 9, "policy_paused": False}})
    with pytest.raises(recovery.RecoveryError, match="not confirmed"):
        recovery.pause()


def test_recovery_receipt_does_not_adopt_reused_child_pid(monkeypatch):
    saved = {"pid": 500000, "start_ticks": 42}
    recovery.remember_targets("arms", saved, [{"pid": 500001, "ticks": 43}])
    monkeypatch.setattr(recovery, "process", lambda pid: None)
    monkeypatch.setattr(recovery, "processes", lambda: [
        {"pid": 500001, "ticks": 90, "ppid": 1, "sid": 500001, "pgid": 500001, "uid": os.getuid()}])
    assert recovery.targets("arms", saved) == []
