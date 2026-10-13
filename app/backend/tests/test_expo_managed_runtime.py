import json
import time
from pathlib import Path
import pytest
from cobot_console.deployment import ManagedRuntime, atomic_json, process_identity
from cobot_console.execution_options import configured_model


def fixture_runtime(tmp_path,monkeypatch):
    runtime=ManagedRuntime(tmp_path)
    model={'id':'expo','kind':'vla','runtime_adapter':'expo_left'}
    atomic_json(runtime.registry,dict(model=model,pid=123,start_ticks=1,started_at=time.time(),ready_confirmed=True))
    monkeypatch.setattr(runtime,'_alive',lambda _:True)
    return runtime,model


def test_ready_requires_current_owner_and_fresh_heartbeat(tmp_path,monkeypatch):
    runtime,model=fixture_runtime(tmp_path,monkeypatch)
    def save(**values):
        atomic_json(tmp_path/'vla-gate.json',dict(ready=True,pid=123,model_id='expo',paused=True,updated_at=time.time(),**values))
    save()
    assert runtime.status()['phase']=='paused'
    data=json.loads((tmp_path/'vla-gate.json').read_text());data['updated_at']-=10
    atomic_json(tmp_path/'vla-gate.json',data)
    assert runtime.status()['phase']=='error'
    save();data=json.loads((tmp_path/'vla-gate.json').read_text());data['pid']=456
    atomic_json(tmp_path/'vla-gate.json',data)
    assert runtime.status()['phase']=='loading'
    save(runtime_fault='deadline missed',intervention_count=3)
    state=runtime.status()
    assert state['phase']=='paused' and state['policy_fault']=='deadline missed' and state['intervention_count']==3


def test_default_execution_has_real_10hz_and_native_rtc():
    model={'kind':'vla','runtime_adapter':'expo_left','control_hz':10}
    value=configured_model(model,{'enabled':False})
    assert value['execution_settings']==dict(enabled=False,publish_hz=10,logical_hz=10,rtc=True,smoothing=False)
    value=configured_model(model,dict(enabled=True,publish_hz=50,rtc=True,smoothing=False))
    assert value['execution_settings']['logical_hz']==10 and value['execution_settings']['publish_hz']==50


def test_expo_catalog_requires_its_loader_norm_and_managed_entry(tmp_path,monkeypatch):
    from cobot_console import deployment,registry
    root=tmp_path/'rl';root.mkdir();monkeypatch.setattr(deployment,'RLT',root)
    entry=root/'scripts/expo_left_up.sh';entry.parent.mkdir();entry.write_text('# fixture')
    checkpoint=tmp_path/'checkpoint';checkpoint.mkdir()
    norm=checkpoint/'norm_stats.json';norm.write_text('{}')
    loader=tmp_path/'deployment.py';loader.write_text('# fixture')
    row=dict(id='expo',kind='vla',runtime_adapter='expo_left',checkpoint=str(checkpoint),required=[str(norm),str(loader)],entry_relative='scripts/expo_left_up.sh',family='EXPO',task='plug_left_2cam',step=2000,control_hz=10,capabilities={'train':False})
    monkeypatch.setattr(registry,'configured_models',lambda _:[row.copy()])
    monkeypatch.setattr(registry,'external_models',lambda:[])
    from cobot_console import model_metadata,model_catalog
    monkeypatch.setattr(model_metadata,'vla_models',lambda:[])
    monkeypatch.setattr(model_catalog.ModelCatalog,'listing',lambda _:{'models':[]})
    value=next(m for m in deployment.catalog() if m['id']=='expo')
    assert value['available'] and value['command']==[str(entry),'expo']
    assert value['family']=='EXPO' and not value['training_enabled']
    norm.unlink()
    assert next(m for m in deployment.catalog() if m['id']=='expo')['available'] is False
