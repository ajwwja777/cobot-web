import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml

ROBOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('selected_home_cli', ROBOT / 'home.py')
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


def test_selected_pose_resolves_five_arms_and_rejects_missing_mid():
    pose = {key: [0.0]*7 for key in ('front_left','front_right','mid','rear_left','rear_right')}
    config = {'poses': {'plug2': pose, 'plug': {'front_left':[0.0]*7,'front_right':[0.0]*7}}}
    selected = home.parse_targets('rear-right,front-left,mid,rear-left,front-right')
    assert selected == home.SELECTABLE_ARMS
    assert set(home.resolve_selected(config, 'plug2', selected)) == {
        'front_left','front_right','mid','rear_left','rear_right'}
    with pytest.raises(home.homing.HomingError, match='missing mid'):
        home.resolve_selected(config, 'plug', selected)


def test_selected_capture_writes_only_requested_arms(tmp_path, monkeypatch):
    path = tmp_path / 'home.yaml'
    path.write_text('poses: {}\n')
    monkeypatch.setattr(home, 'measured', lambda topic: [0.1]*7)
    args = NS(arm='front', targets='front-left,rear-right', include_rear=False,
              pose='side_pair', config=str(path))
    assert home.do_capture(args, {})
    assert set(yaml.safe_load(path.read_text())['poses']['side_pair']) == {'front_left','rear_right'}


def test_selected_front_holds_unselected_front_and_skips_other_owners(monkeypatch):
    calls = []
    target = {'front_left': (0.2,)*7}
    monkeypatch.setattr(home, 'measured', lambda topic: (0.1,)*7)
    monkeypatch.setattr(home, 'push_params', lambda values, config: calls.append(('params', dict(values))))
    monkeypatch.setattr(home.rospy, 'wait_for_service', lambda service, timeout: calls.append(('wait',service)))
    monkeypatch.setattr(home.rospy, 'ServiceProxy', lambda service, kind: lambda: NS(success=True,message='ok'))
    monkeypatch.setattr(home, 'verify_front_arrival', lambda values: True)
    assert home.do_selected(target, {}, ('front-left',))
    assert ('wait', home.FRONT_HOME_SERVICE) in calls
    assert not any(item[0]=='wait' and 'rear' in item[1] for item in calls)
    assert calls[-1][1]['front_right'] == (0.1,)*7
