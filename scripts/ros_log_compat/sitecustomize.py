"""ROS Noetic latest-log update: atomic replacement for simultaneous nodes."""
import os
import uuid

def renew_latest_logdir(logfile_dir):
    parent = os.path.dirname(logfile_dir)
    latest = os.path.join(parent, "latest")
    if os.path.lexists(latest) and not os.path.islink(latest):
        return False
    temporary = os.path.join(parent, ".latest-" + str(os.getpid()) + "-" + uuid.uuid4().hex)
    try:
        os.symlink(logfile_dir, temporary)
        os.replace(temporary, latest)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    return True

try:
    import rosgraph.roslogging
except ImportError:
    pass
else:
    rosgraph.roslogging.renew_latest_logdir = renew_latest_logdir
