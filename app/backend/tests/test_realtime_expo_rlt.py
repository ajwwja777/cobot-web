from cobot_console import deployment

def test_left_online_evaluation_keeps_left_adapter_and_actor():
    model=dict(id='realtime-expo-rlt-online',kind='rlt',mode='online',io_contract='rlt-left-arm-2cam-10hz',adapter_id='realtime-expo-rlt',checkpoint='/new/online/actor_snapshot/actor_snapshot.pkl',training_enabled=True)
    value=deployment.model_for_use(model,'evaluation')
    assert value['adapter_id']=='realtime-expo-rlt'
    assert value['mode']=='frozen' and value['checkpoint']==model['checkpoint']
    assert not value['training_enabled'] and model['training_enabled']

def test_left_collection_uses_explicit_selected_mode():
    for mode in ('frozen','online'):
        model=dict(id='realtime-expo-rlt-'+mode,kind='rlt',mode=mode,io_contract='rlt-left-arm-2cam-10hz',adapter_id='realtime-expo-rlt',training_enabled=mode=='online')
        result=deployment.model_for_use(model,'collection')
        assert result['mode']==mode and result['training_enabled']==(mode=='online')
