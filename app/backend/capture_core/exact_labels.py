"""Exact immutable episode lookup for the RLT finalization fast path."""
from pathlib import Path
from uuid import UUID

from .labels import LabelStore, LabelValidationError, LabelNotFoundError
from .schema import IDENTIFIER_RE
import re

class ExactEpisodeLabelStore(LabelStore):
    def __init__(self, data_root, relative_path):
        super().__init__(data_root)
        relative = Path(relative_path)
        if (relative.is_absolute() or len(relative.parts) not in {1, 4}
            or any(not IDENTIFIER_RE.fullmatch(part) for part in relative.parts[:-1])
            or not re.fullmatch(r"episode_[0-9]{6}[.]hdf5", relative.parts[-1])):
            raise LabelValidationError("invalid_episode_relative_path")
        self._episode_path = self.data_root / relative
        if not self._is_safe_regular_file(self._episode_path):
            raise LabelValidationError("unsafe_episode_path")

    def _find_record(self, episode_uuid):
        try:
            wanted = UUID(str(episode_uuid))
        except (ValueError, TypeError, AttributeError) as error:
            raise LabelNotFoundError("episode_not_found") from error
        record = self._read_record(self._episode_path)
        if record is None or record.episode_uuid != wanted:
            raise LabelNotFoundError("episode_not_found")
        return record