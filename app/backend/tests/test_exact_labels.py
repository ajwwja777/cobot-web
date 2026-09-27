from pathlib import Path
from uuid import uuid4
import pytest
from tests.test_labels import _write_episode

def test_exact_store_reads_one_file_and_never_discovers_history(tmp_path, monkeypatch):
    from capture_core.exact_labels import ExactEpisodeLabelStore
    path,uuid=_write_episode(tmp_path,index=7)
    _write_episode(tmp_path,index=8)
    store=ExactEpisodeLabelStore(tmp_path,str(path.relative_to(tmp_path)))
    monkeypatch.setattr(store,"_records",lambda: (_ for _ in ()).throw(AssertionError("history scan")))
    result=store.set_outcome(uuid,"success")
    assert result["episode_outcome"]=="success"

def test_exact_store_rejects_traversal_and_wrong_uuid(tmp_path):
    from capture_core.exact_labels import ExactEpisodeLabelStore
    from capture_core.labels import LabelValidationError,LabelNotFoundError
    path,uuid=_write_episode(tmp_path,index=7)
    for unsafe in ("../episode_000007.hdf5","/tmp/episode_000007.hdf5","a/b/c/not-an-episode"):
        with pytest.raises(LabelValidationError):
            ExactEpisodeLabelStore(tmp_path,unsafe)
    store=ExactEpisodeLabelStore(tmp_path,str(path.relative_to(tmp_path)))
    with pytest.raises(LabelNotFoundError):
        store.get_labels(uuid4())