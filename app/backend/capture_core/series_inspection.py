"""Bounded inspection of the latest episode in one prepared series."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from .labels import LabelStore, LabelValidationError, labels_are_complete
from .storage import PreparedSeries, SeriesIdentity

_FINALIZED_RE = re.compile(r"^episode_(?P<index>[0-9]{6})\.hdf5$")
_INCOMPLETE_RE = re.compile(r"^episode_(?P<index>[0-9]{6})\.hdf5\.incomplete$")


def _safe_regular_file(path: Path, parent: Path) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.resolve() == path
            and path.parent.resolve() == parent
        )
    except OSError:
        return False


def inspect_prepared_series(
    store: LabelStore,
    prepared: PreparedSeries,
    identity: SeriesIdentity,
) -> Dict[str, object]:
    """Inspect only the latest file while preserving the forward label gate."""
    if not isinstance(store, LabelStore):
        raise TypeError("store must be LabelStore")
    if not isinstance(prepared, PreparedSeries):
        raise TypeError("prepared must be PreparedSeries")
    if not isinstance(identity, SeriesIdentity):
        raise TypeError("identity must be SeriesIdentity")

    directory = prepared.episode_directory.resolve()
    finalized = {}
    incomplete = set()
    try:
        for candidate in directory.iterdir():
            if not _safe_regular_file(candidate, directory):
                continue
            final_match = _FINALIZED_RE.fullmatch(candidate.name)
            if final_match is not None:
                finalized[int(final_match.group("index"))] = candidate
                continue
            incomplete_match = _INCOMPLETE_RE.fullmatch(candidate.name)
            if incomplete_match is not None:
                incomplete.add(int(incomplete_match.group("index")))
    except OSError as error:
        raise LabelValidationError("series_inspection_failed") from error

    latest_finalized = max(finalized) if finalized else None
    latest_incomplete = max(incomplete) if incomplete else None
    if latest_incomplete is not None and (
        latest_finalized is None or latest_incomplete >= latest_finalized
    ):
        raise LabelValidationError("latest_episode_incomplete")

    if latest_finalized is None:
        return {
            "episode_count": 0,
            "latest_episode_uuid": None,
            "latest_episode_index": None,
            "latest_labels_complete": True,
            "label_blocked": False,
        }

    with store._lock:
        record = store._read_record(finalized[latest_finalized])
        if record is None or record.episode_index != latest_finalized:
            raise LabelValidationError("latest_episode_invalid")
        if identity.storage_layout != "flat" and (
            record.task_id, record.model_id, record.dataset_round
        ) != (identity.task_id, identity.model_id, identity.dataset_round):
            raise LabelValidationError("latest_episode_invalid")
        complete = labels_are_complete(store._read_labels(record))

    return {
        "episode_count": len(finalized),
        "latest_episode_uuid": str(record.episode_uuid),
        "latest_episode_index": record.episode_index,
        "latest_labels_complete": complete,
        "label_blocked": not complete,
    }
