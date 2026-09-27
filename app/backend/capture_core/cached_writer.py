"""RLT-local HDF5 handle cache; preserves the upstream schema and lifecycle."""
from capture_core.hdf5_writer import Hdf5EpisodeWriter


class _CachedDatasetFile:
    def __init__(self, stream):
        self._stream = stream
        self._datasets = {}

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __getitem__(self, path):
        if path not in self._datasets:
            self._datasets[path] = self._stream[path]
        return self._datasets[path]

    def close(self):
        # Release dataset handles before closing or reopening the file.
        self._datasets.clear()
        self._stream.close()


class CachedEpisodeWriter(Hdf5EpisodeWriter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The stock writer reopens ~90 datasets per frame, evicting their
        # partial chunk caches. Keep handles alive for this episode only.
        self._file = _CachedDatasetFile(self._file)
