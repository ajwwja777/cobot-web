from types import SimpleNamespace
import json
import pytest
from cobot_console.execution_options import configured_model
from cobot_console import deployment

@pytest.mark.parametrize('kind',['rlt','pi05','vla'])
def test_common_choices_reach_each_managed_launch_environment(tmp_path,monkeypatch,kind):
    options=dict(enabled=True,publish_hz=50,rtc=False,smoothing=True)
    model=configured_model(dict(id='test',kind=kind,checkpoint='/fixed.pkl',control_hz=20),options)
    assert model['execution_settings']['publish_hz']==50
    assert model['execution_settings']['logical_hz']==20
    assert model['execution_settings']['rtc'] is False
    calls=[]
    monkeypatch.setattr(deployment.subprocess,'Popen',lambda command,**kw:(calls.append((command,kw)) or SimpleNamespace(pid=999999)))
    monkeypatch.setattr('cobot_console.device_control._default_process_finder',lambda marker:[])
    runtime=deployment.ManagedRuntime(tmp_path/'runtime')
    monkeypatch.setattr(runtime,'status',lambda:{})
    monkeypatch.setattr(runtime,'_alive',lambda state:False)
    monkeypatch.setenv('COBOT_RLT_EXECUTION_OPTIONS','stale')
    runtime.load(model)
    env=calls[0][1]['env']
    assert json.loads(env['COBOT_EXECUTION_OPTIONS'])==options
    assert ('COBOT_RLT_EXECUTION_OPTIONS' in env)==(kind=='rlt')

def test_invalid_options_fail_before_adapter_dispatch():
    with pytest.raises(ValueError):configured_model(dict(kind='pi05'),dict(enabled=True,publish_hz=True,rtc=False,smoothing=False))
