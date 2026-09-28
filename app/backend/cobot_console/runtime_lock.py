"""Cross-process serialization for CLI and web model operations."""
import fcntl
import functools
import threading
from contextlib import contextmanager
_guard = threading.RLock()
_local = threading.local()
@contextmanager
def operation(directory):
    with _guard:
        if getattr(_local, "depth", 0):
            yield
            return
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / ".model-operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _local.depth = 1
            try: yield
            finally:
                _local.depth = 0
                fcntl.flock(lock, fcntl.LOCK_UN)
def serialized(function):
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        with operation(self.directory):
            return function(self, *args, **kwargs)
    return wrapped
