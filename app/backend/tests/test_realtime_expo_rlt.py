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


def test_left_reference_remains_reference_for_both_uses():
    model=dict(id='realtime-expo-rlt-reference',kind='rlt',mode='reference',
               io_contract='rlt-left-arm-2cam-10hz',adapter_id='realtime-expo-rlt',
               checkpoint='/stage1',training_enabled=False)
    for use in ('evaluation','collection'):
        value=deployment.model_for_use(model,use)
        assert value['mode']=='reference'
        assert value['checkpoint']=='/stage1' and not value['training_enabled']
        assert value['adapter_id']=='realtime-expo-rlt'


def test_reference_catalog_checks_stage1_not_actor_and_has_no_actor_identity(tmp_path,monkeypatch):
    from cobot_console import registry,model_catalog,rlt_progress
    stage=tmp_path/'stage1';(stage/'params').mkdir(parents=True)
    item=dict(id='realtime-expo-rlt-reference',kind='rlt',mode='reference',
              checkpoint=str(stage),base_checkpoint=str(stage),custom=True,
              checkpoint_type='stage1_reference',io_contract='rlt-left-arm-2cam-10hz',
              stage1_step=4999,actor_version=3500,capabilities=dict(train=False))
    monkeypatch.setattr(registry,'configured_models',lambda *_:[dict(item)])
    monkeypatch.setattr(model_catalog.ModelCatalog,'listing',lambda *_:dict(models=[]))
    monkeypatch.setattr(rlt_progress,'published_actor',lambda *_:(_ for _ in ()).throw(AssertionError('Reference has no Actor')))
    result=next(x for x in deployment.catalog() if x['id']==item['id'])
    assert result['available'] and result['step']==4999
    assert result['actor_version'] is None and result['learner_step'] is None
    assert not result['publication_tracked'] and not result['training_enabled']
