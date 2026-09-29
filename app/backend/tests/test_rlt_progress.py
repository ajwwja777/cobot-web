import pickle
from pathlib import Path
from cobot_console.rlt_progress import published_actor
from cobot_console.deployment import DeploymentManager


def test_snapshot_identity_is_publication_not_training_progress(tmp_path):
    path = tmp_path / "actor.pkl"
    path.write_bytes(pickle.dumps(dict(version=3250, global_step=6500,
                                      actor_params={"opaque":"weights"}), protocol=5))
    result = published_actor(path)
    assert result["published_learner_step"] == 6500
    assert result["published_actor_version"] == 3250


def test_unknown_or_executable_pickle_is_not_deserialized(tmp_path):
    class Dangerous:
        def __reduce__(self):
            return (eval, ("1/0",))
    path = tmp_path / "actor.pkl"
    for payload in [pickle.dumps(Dangerous()), b"truncated",
                    pickle.dumps({"global_step":6915}),
                    pickle.dumps({"version":-1,"global_step":6500})]:
        path.write_bytes(payload)
        assert published_actor(path) == {}
    assert published_actor(tmp_path / "missing") == {}


def test_catalog_distinguishes_learner_and_published_actor(tmp_path, monkeypatch):
    import json
    import cobot_console.model_catalog as catalog
    run = tmp_path / "run"
    metrics = run / "online/metrics/learner_status.json"
    metrics.parent.mkdir(parents=True)
    metrics.write_text(json.dumps(dict(global_step=6915,actor_version=3457,ready_for_online=True)))
    snapshot = tmp_path / "models/online/actor_snapshot/actor_snapshot.pkl"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(pickle.dumps(dict(version=3250,global_step=6500),protocol=5))
    monkeypatch.setattr(catalog,"V3_RUN",run)
    monkeypatch.setattr(catalog,"RLT_MODELS",tmp_path/"models")
    monkeypatch.setattr(catalog.ModelCatalog,"_parameters",lambda *args:[])
    rows=catalog.ModelCatalog("plug_v3_yyshadow")._models()
    for row in rows[1:]:
        assert row["learner_step"] == 6915
        assert row["learner_actor_version"] == 3457
        assert row["step"] == row["published_learner_step"] == 6500
        assert row["actor_version"] == 3250
