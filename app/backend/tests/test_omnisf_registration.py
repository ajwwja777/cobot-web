import json
import time
import pytest
from cobot_console import model_metadata, deployment
from cobot_console.execution_options import configured_model


def test_omnisf_native_options_are_truthful_and_strict():
    model=dict(kind='vla',runtime_adapter='omnisf',control_hz=30,execution_options_supported=False)
    actual=configured_model(model, {'enabled': False})['execution_settings']
    assert actual == dict(enabled=False,publish_hz=30,logical_hz=30,rtc=False,smoothing=False)
    with pytest.raises(ValueError,match='native 30 Hz'):
        configured_model(model,dict(enabled=True,publish_hz=50,rtc=True,smoothing=True))


def test_omnisf_catalog_retains_native_command_and_contract(tmp_path,monkeypatch):
    source=(model_metadata.VLA/'integrations/cobot/registry.py').read_text()
    monkeypatch.setattr(model_metadata,'VLA',tmp_path)
    root=tmp_path/'integrations/cobot/omnisf';root.mkdir(parents=True)
    (root.parent/'registry.py').write_text(source)
    (root/'interface.sh').write_text('#!/bin/sh')
    ckpt=tmp_path/'checkpoint';ckpt.mkdir()
    (tmp_path/'configs').mkdir()
    row=dict(id='omnisf',family='OmniSF',task='cover_blocks',step=30000,
             root='integrations/cobot/omnisf',checkpoint=str(ckpt),required=[],runtime_dependencies=[],
             managed=True,args=['interface.sh'],home_pose='origin',control_hz=30,
             runtime_adapter='omnisf',execution_options_supported=False,prompt='cover blocks')
    (tmp_path/'configs/cobot_models.json').write_text(json.dumps({'models':[row]}))
    actual=model_metadata.vla_models()[0]
    assert actual['available'] and actual['runtime_adapter']=='omnisf'
    assert actual['execution_options_supported'] is False and actual['prompt']=='cover blocks'
    assert 'interface.sh' in actual['cli_command'] and 'interface_task2_teach_rtc_live' not in actual['cli_command']


def test_omnisf_readiness_belongs_to_fresh_supervisor(tmp_path,monkeypatch):
    runtime=deployment.ManagedRuntime(tmp_path)
    deployment.atomic_json(runtime.registry,dict(model=dict(id='omnisf',kind='vla',runtime_adapter='omnisf'),pid=123,started_at=time.time()))
    monkeypatch.setattr(runtime,'_alive',lambda _:True)
    gate=dict(ready=True,paused=True,pid=123,model_id='omnisf',updated_at=time.time())
    deployment.atomic_json(tmp_path/'vla-gate.json',gate)
    assert runtime.status()['phase']=='paused'
    deployment.atomic_json(tmp_path/'vla-gate.json',{**gate,'updated_at':time.time()-10})
    assert runtime.status()['phase']=='error'
