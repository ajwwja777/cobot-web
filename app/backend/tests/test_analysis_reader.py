import json
from cobot_console import analysis

def test_reader_reads_fixed_paths_and_caches_without_exposing_process_environment(tmp_path, monkeypatch):
    project=tmp_path/"rl"
    cfg=project/"configs/rlt/plug_v3_yyshadow"
    cfg.mkdir(parents=True)
    (cfg/"online_rl.yaml").write_text("runtime: {}\n")
    run=project/"outputs/rlt/plug_v3_yyshadow"
    metrics=run/"online/metrics";metrics.mkdir(parents=True)
    (metrics/"learner_status.json").write_text(json.dumps({"global_step":123,"timestamp":1}))
    runtime=tmp_path/"web";(runtime/"deployment").mkdir(parents=True)
    (runtime/"deployment/process.json").write_text(json.dumps({"pid":12,"secret":"not returned","model":{"id":"x","checkpoint":"/weights","secret":"hidden"}}))
    monkeypatch.setattr(analysis,"RLT",project)
    monkeypatch.setattr(analysis,"RUNTIME_ROOT",runtime)
    monkeypatch.setenv("COBOT_RLT_RUN_ROOT",str(run))
    reader=analysis.AnalysisReader()
    result=reader.snapshot()
    assert result["status"]["global_step"]==123
    assert result["stale"]
    assert "secret" not in json.dumps(result)
    (metrics/"learner_status.json").write_text("{}")
    assert reader.snapshot() is result
