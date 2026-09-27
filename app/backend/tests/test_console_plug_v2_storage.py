from capture_core.storage import SeriesIdentity,prepare_series
def test_conversion_reserves_identity_after_raw_removed(tmp_path):
    series=SeriesIdentity('plug','rlt','plug_v2','flat')
    (tmp_path/'episode_000003.rlt.json').write_text('{}')
    (tmp_path/'episode_000003.labels.json').write_text('{}')
    prepared=prepare_series(tmp_path,series)
    assert prepared.next_episode_index==4
    assert prepared.existing_indices==()
def test_live_source_still_resolves_normal_labels(tmp_path):
    series=SeriesIdentity('plug','rlt','plug_v2','flat')
    (tmp_path/'episode_000001.rlt.json').write_text('{}')
    (tmp_path/'episode_000002.hdf5').write_bytes(b'')
    prepared=prepare_series(tmp_path,series)
    assert prepared.next_episode_index==3 and prepared.existing_indices==(2,)
