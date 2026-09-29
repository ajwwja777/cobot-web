import json
from cobot_console.model_directories import model_directories

def test_registration_overrides_site_defaults_without_guessing_from_weights(tmp_path):
    config=tmp_path/"dirs.json"
    config.write_text(json.dumps({"models":{"m":{"collection":"{DATA}/datasets/plug/online","evaluation":"{DATA}/evaluations/plug/online"}}}))
    values=model_directories({"id":"m","checkpoint":"/weights/warmup",
        "data_directories":{"collection":"{DATA}/datasets/plug/custom"}},config,tmp_path)
    assert values==dict(collection=str(tmp_path/"datasets/plug/custom"),evaluation=str(tmp_path/"evaluations/plug/online"))
    assert model_directories({"id":"unknown","checkpoint":"/weights/warmup"},config,tmp_path)=={}

def test_relative_unknown_path_is_not_applied(tmp_path):
    config=tmp_path/"empty.json";config.write_text("{}")
    assert model_directories({"id":"x","data_directories":{"collection":"relative/path"}},config,tmp_path)=={}
