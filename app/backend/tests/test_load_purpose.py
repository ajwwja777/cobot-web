from pathlib import Path
import pytest
from cobot_console import deployment, execution_options


def test_fixed_step_collection_creates_a_distinct_learning_target_without_touching_seed(tmp_path, monkeypatch):
    seed=tmp_path/'seed'
    (seed/'checkpoints').mkdir(parents=True)
    (seed/'checkpoints/latest.pkl').write_bytes(b'checkpoint')
    monkeypatch.setattr(deployment,'RLT_MODELS',tmp_path/'models')
    monkeypatch.setattr(deployment,'RUN',tmp_path/'run')
    fixed=dict(id='plug-v3-warmup-5k',kind='rlt',checkpoint=str(seed/'actor_snapshot/actor_snapshot.pkl'),online_seed_available=True,training_enabled=False)
    online=deployment.model_for_use(fixed,'collection')
    assert online['training_enabled'] and online['id']!=fixed['id']
    assert online['online_seed']==str(seed) and online['source_step']==5000
    assert online['checkpoint']!=fixed['checkpoint'] and not Path(online['checkpoint']).exists()
    assert fixed['training_enabled'] is False
    frozen=deployment.model_for_use(online,'evaluation')
    assert not frozen['training_enabled'] and frozen['evaluation_allowed']
    assert frozen['checkpoint']==online['checkpoint'] and frozen['mode']=='frozen'


def test_collection_refuses_missing_full_seed_and_evaluation_keeps_selected_mc30_weights(tmp_path):
    with pytest.raises(deployment.DeploymentError):deployment.model_for_use(dict(id='fixed',kind='rlt',checkpoint=str(tmp_path/'actor_snapshot/a.pkl'),online_seed_available=True),'collection')
    model=dict(id='mc30',kind='rlt',checkpoint='/candidate/actor_snapshot/actor_snapshot.pkl',runtime_profile='credit_mc30',training_enabled=True,mode='online')
    fixed=deployment.model_for_use(model,'evaluation')
    assert fixed['checkpoint']==model['checkpoint'] and fixed['runtime_profile']=='credit_mc30'
    assert fixed['training_enabled'] is False and model['training_enabled'] is True


def test_branch_catalog_follows_published_steps_and_keeps_fixed5000(tmp_path,monkeypatch):
    import json,pickle,shutil
    from tests.test_deployment_evaluation import mock_assets
    mock_assets(monkeypatch,tmp_path)
    source=Path(__file__).resolve().parents[4]/'rl-platform/integrations/cobot_runtime/online_seed.py'
    module=deployment.RLT/'integrations/cobot_runtime/online_seed.py'
    module.parent.mkdir(parents=True);shutil.copyfile(source,module)
    branch=deployment.RLT_MODELS/'online_from_5000/run1'
    actor=branch/'actor_snapshot/actor_snapshot.pkl';actor.parent.mkdir(parents=True)
    actor.write_bytes(pickle.dumps(dict(version=2600,global_step=5200),protocol=5))
    (branch/'seed.json').write_text(json.dumps(dict(model_id='plug-v3-warmup-5k',source_step=5000,run_root=str(tmp_path/'branch-run'),config_target=str(tmp_path/'branch-config.yaml'))))
    (branch/'action_norm_stats.json').write_text('{}')
    rows={m['id']:m for m in deployment.catalog()}
    assert rows['plug-v3-warmup-5k']['step']==5000
    updated=rows['plug-v3-from-5000-run1']
    assert updated['step']==updated['published_learner_step']==5200 and updated['training_enabled']
    assert updated['checkpoint']==str(actor) and updated['training_method']=='original'
    actor.write_bytes(pickle.dumps(dict(version=2700,global_step=5400),protocol=5))
    rows={m['id']:m for m in deployment.catalog()}
    assert rows['plug-v3-from-5000-run1']['step']==5400
    assert rows['plug-v3-warmup-5k']['step']==5000
