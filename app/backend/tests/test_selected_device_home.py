from pathlib import Path

import pytest
import yaml

from cobot_console.control_import import control_package
control_package()
from cobot_control import device_control as dc


def test_selected_home_and_capture_are_exactly_allowlisted(tmp_path, monkeypatch):
    config = tmp_path / 'home.yaml'
    config.write_text(yaml.safe_dump({'poses': {
        'plug': {'front_left': [0]*7, 'front_right': [0]*7},
        'plug2': {key: [0]*7 for key in ('front_left','front_right','mid','rear_left','rear_right')},
    }}))
    monkeypatch.setattr(dc, 'POSE_CONFIG', config)
    controller = dc.DeviceController(tmp_path / 'jobs', system_probe=lambda: {})
    selected = ['front-left','front-right','mid','rear-left','rear-right']
    normalized = controller._normalize({'component':'home','action':'run','target':'selection',
                                        'arms':selected,'pose':'plug2'})
    assert controller._command(normalized)[1:] == ['selected','--targets',','.join(selected),'--pose','plug2','--yes']
    with pytest.raises(dc.DeviceControlError, match='not available'):
        controller._normalize({'component':'home','action':'run','target':'selection',
                               'arms':selected,'pose':'plug'})
    with pytest.raises(dc.DeviceControlError, match='duplicate'):
        controller._normalize({'component':'home','action':'run','target':'selection',
                               'arms':['mid','mid'],'pose':'plug2'})
    capture = controller._normalize({'component':'pose','action':'capture','target':'selection',
                                     'arms':['front-left','rear-right'],'pose':'side_pair'})
    assert controller._command(capture)[1:] == ['capture','--targets','front-left,rear-right','--pose','side_pair']
    deletion = controller._normalize({'component':'pose','action':'delete','target':'selection',
                                      'arms':['front-left','front-right'],'pose':'plug'})
    assert controller._command(deletion)[1:] == ['delete','--pose','plug','--yes']
    paired_recovery = controller._normalize({'component':'recover','action':'run','target':'front-pair'})
    assert controller._command(paired_recovery)[1:] == ['front-pair','--supported']


def test_rear_teach_and_idle_disabled_are_healthy():
    def feedback(mode, teach, enabled):
        arm = bytearray(8)
        arm[0], arm[3] = mode, teach
        motor = bytearray(8)
        motor[5] = 0x40 if enabled else 0
        return {0x2a1:bytes(arm), 0x2a8:bytes(8),
                **{number:bytes(motor) for number in range(0x261,0x267)}}

    teaching = dc._classify_arm_feedback('can_rear_left', feedback(2,2,True))
    idle = dc._classify_arm_feedback('can_rear_left', feedback(0,0,False))
    holding = dc._classify_arm_feedback('can_rear_left', feedback(1,0,True))
    unexpected = dc._classify_arm_feedback('can_rear_left', feedback(2,0,True))
    assert (teaching['phase'],teaching['rear_mode']) == ('ready','teaching')
    assert (idle['phase'],idle['rear_mode']) == ('ready','idle_disabled')
    assert (holding['phase'],holding['rear_mode']) == ('ready','can_holding')
    assert (unexpected['phase'],unexpected['rear_mode']) == ('error','unexpected')
