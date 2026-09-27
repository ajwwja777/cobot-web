# Read-only reconnect wrapper for the unified console profile.
import threading
import os

from capture_core.ros_subscriber import RosSubscriberBridge


class ReconnectingBridge(RosSubscriberBridge):
    _RECONNECT_ERRORS = {
        'ros_master_changed',
        'ros_master_unavailable',
        'ros_node_unregistered',
        'ros_unavailable',
        'ros_subscriber_error',
        'ros_callback_error',
    }

    def __init__(self, *args, retry_seconds=1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._retry_seconds = retry_seconds
        self._end = threading.Event()
        self._thread = None

    def start(self):
        if os.environ.get("COBOT_READ_ONLY") == "1":
            return
        if self._thread is not None:
            return
        super().start()
        self._thread = threading.Thread(
            target=self._retry,
            name='console-ros-reconnect',
            daemon=True,
        )
        self._thread.start()

    def _retry(self):
        while not self._end.wait(self._retry_seconds):
            state = super().status()
            if state['state'] == 'ready':
                continue
            # Cameras remain independently useful while the arm coordinator is
            # offline. Missing handover publishers must not tear down healthy
            # camera subscriptions or clear their latest frames.
            if state.get('error_code') == 'handover_publisher_unavailable':
                continue
            if state.get('error_code') not in self._RECONNECT_ERRORS:
                continue
            super().shutdown()
            self._cache.clear()
            super().start()

    def shutdown(self):
        self._end.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        super().shutdown()
