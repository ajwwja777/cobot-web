"""Read publication identity without deserializing executable checkpoint content."""
import os
import pickletools
from pathlib import Path


def published_actor(path):
    """Only accept the bounded scalar prefix emitted by the RLT snapshot writer.

    Never unpickle weights in the web process. Unknown layouts stay unknown.
    """
    try:
        with Path(path).open("rb") as stream:
            prefix = stream.read(512)
            timestamp = os.fstat(stream.fileno()).st_mtime
        values = {}
        key = None
        for op, arg, _ in pickletools.genops(prefix):
            if op.name in {"PROTO", "FRAME", "EMPTY_DICT", "MEMOIZE", "MARK"}:
                continue
            if op.name in {"SHORT_BINUNICODE", "BINUNICODE"}:
                if arg not in {"version", "global_step"} or key is not None:
                    return {}
                key = arg
            elif op.name in {"BININT", "BININT1", "BININT2", "LONG1", "LONG4"}:
                if key is None or type(arg) is not int or arg < 0:
                    return {}
                values[key] = arg
                key = None
                if len(values) == 2:
                    return {"published_actor_version": values["version"],
                            "published_learner_step": values["global_step"],
                            "published_at": timestamp}
            else:
                return {}
    except (OSError, ValueError):
        pass
    return {}
