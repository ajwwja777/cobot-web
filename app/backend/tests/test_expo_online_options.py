import pytest
from cobot_console.execution_options import configured_model, normalize_options


def model():
    return dict(id='expo',kind='vla',command=['/fake/launcher'],cwd='/tmp',runtime_adapter='expo_online',expo_online_options={'defaults':{'rtc_delay':5,'edit_scale':.01}})

def choices():
    return dict(enabled=True,publish_hz=50,rtc=True,smoothing=True,expo=dict(rtc_delay=3,edit_scale=.07))

def test_route_model_and_launch_environment_preserve_both_parameters(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from cobot_console import deployment
    options=normalize_options(choices());configured=configured_model(model(),options)
    assert configured['execution_settings']['expo']==dict(rtc_delay=3,edit_scale=.07)
    calls=[]
    monkeypatch.setattr(deployment.subprocess,'Popen',lambda command,**kw:(calls.append(kw) or SimpleNamespace(pid=999999)))
    monkeypatch.setattr('cobot_console.device_control._default_process_finder',lambda marker:[])
    runtime=deployment.ManagedRuntime(tmp_path/'runtime')
    monkeypatch.setattr(runtime,'status',lambda:{})
    monkeypatch.setattr(runtime,'_alive',lambda state:False)
    runtime.load(configured)
    assert json.loads(calls[0]['env']['COBOT_EXECUTION_OPTIONS'])==choices()

def test_defaults_and_non_expo_rejection():
    assert configured_model(model())['execution_settings']['expo']==dict(rtc_delay=5,edit_scale=.01)
    for other in (dict(kind='rlt'),dict(kind='vla',runtime_adapter='expo_left'),dict(kind='vla',runtime_adapter='expo_online')):
        with pytest.raises(ValueError):configured_model(other,choices())

@pytest.mark.parametrize('extra',[None,{},dict(rtc_delay=0,edit_scale=.01),dict(rtc_delay=5,edit_scale=.015)])
def test_bad_options_rejected_before_launch(extra):
    with pytest.raises(ValueError):normalize_options({**choices(),'expo':extra})
