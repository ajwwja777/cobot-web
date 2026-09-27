"""Lazy, read-only ROS subscriber wiring for the production recorder."""

from __future__ import annotations

import importlib
import math
import os
import socket
import time
import xmlrpc.client
from dataclasses import dataclass
from threading import RLock
from typing import Any
from urllib.parse import urlparse

import numpy as np

from .ros_cache import LatestMessageCache
from .topics import (
    CAMERA_KEYS,
    COORDINATOR_KEYS,
    FRONT_KEYS,
    POLICY_KEYS,
    REAR_KEYS,
    REQUIRED_TOPICS,
)

_JOINT_KEYS = FRONT_KEYS | REAR_KEYS | POLICY_KEYS | COORDINATOR_KEYS
_BOOL_KEYS = frozenset({"teach_left", "teach_right"})
_TEXT_KEYS = frozenset({"handover_mode", "handover_fault"})


@dataclass(frozen=True)
class RosBindings:
    """Runtime-only ROS modules and message classes."""

    rospy: Any
    cv_bridge_factory: Any
    image_type: Any
    joint_state_type: Any
    string_type: Any
    bool_type: Any


def _load_ros_bindings() -> RosBindings:
    """Import ROS only when application lifespan starts."""
    rospy = importlib.import_module("rospy")
    cv_bridge = importlib.import_module("cv_bridge")
    sensor_messages = importlib.import_module("sensor_msgs.msg")
    standard_messages = importlib.import_module("std_msgs.msg")
    return RosBindings(
        rospy=rospy,
        cv_bridge_factory=cv_bridge.CvBridge,
        image_type=sensor_messages.Image,
        joint_state_type=sensor_messages.JointState,
        string_type=standard_messages.String,
        bool_type=standard_messages.Bool,
    )


def _ros_master_reachable(timeout_seconds: float = 0.5) -> bool:
    """Bound the read-only TCP probe performed before ``rospy.init_node``."""
    endpoint = urlparse(os.environ.get("ROS_MASTER_URI", "http://127.0.0.1:11311"))
    if endpoint.scheme not in {"http", "https"} or endpoint.hostname is None:
        return False
    try:
        port = endpoint.port
    except ValueError:
        return False
    if port is None:
        return False
    try:
        with socket.create_connection(
            (endpoint.hostname, port), timeout=timeout_seconds
        ):
            return True
    except OSError:
        return False


class _TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self._timeout_seconds = float(timeout_seconds)

    def make_connection(self, host: str) -> Any:
        connection = super().make_connection(host)
        connection.timeout = self._timeout_seconds
        return connection


def _configured_master_uri() -> str:
    return os.environ.get("ROS_MASTER_URI", "http://127.0.0.1:11311")


def _ros_node_registered(
    master_uri: str,
    node_name: str,
    topics: dict[str, str],
    timeout_seconds: float = 0.5,
) -> bool:
    """Check this node is a subscriber in the current master's system state."""
    try:
        proxy = xmlrpc.client.ServerProxy(
            master_uri,
            allow_none=True,
            transport=_TimeoutTransport(timeout_seconds),
        )
        code, _message, system_state = proxy.getSystemState(node_name)
        if int(code) != 1 or not isinstance(system_state, (list, tuple)):
            return False
        subscribers = system_state[1]
        registered = {
            str(topic): {str(name) for name in names}
            for topic, names in subscribers
        }
        return all(node_name in registered.get(topic, set()) for topic in topics.values())
    except Exception:  # noqa: BLE001 - health probes fail closed
        return False


def _ros_mode_publisher_live(
    master_uri: str, node_name: str, timeout_seconds: float = 0.5
) -> bool:
    """Latched state stays valid only while its unique current publisher is live."""
    try:
        master = xmlrpc.client.ServerProxy(
            master_uri, allow_none=True,
            transport=_TimeoutTransport(timeout_seconds),
        )
        code, _message, system_state = master.getSystemState(node_name)
        if int(code) != 1:
            return False
        mode_nodes = [
            str(name)
            for topic, names in system_state[0]
            if topic == REQUIRED_TOPICS["handover_mode"]
            for name in names
        ]
        if len(mode_nodes) != 1:
            return False
        code, _message, uri = master.lookupNode(node_name, mode_nodes[0])
        if int(code) != 1 or not isinstance(uri, str):
            return False
        publisher = xmlrpc.client.ServerProxy(
            uri, allow_none=True,
            transport=_TimeoutTransport(timeout_seconds),
        )
        code, _message, pid = publisher.getPid(node_name)
        return int(code) == 1 and int(pid) > 0
    except Exception:  # noqa: BLE001 - cached mode alone must never imply liveness
        return False


class RosSubscriberBridge:
    """Populate one :class:`LatestMessageCache` using subscriber callbacks only."""

    def __init__(
        self,
        cache: LatestMessageCache,
        *,
        bindings_loader: Any = _load_ros_bindings,
        monotonic: Any = time.monotonic,
        master_probe: Any = _ros_master_reachable,
        master_uri_getter: Any = _configured_master_uri,
        registration_probe: Any = _ros_node_registered,
        mode_publisher_probe: Any = _ros_mode_publisher_live,
    ) -> None:
        self._cache = cache
        self._bindings_loader = bindings_loader
        self._monotonic = monotonic
        self._master_probe = master_probe
        self._master_uri_getter = master_uri_getter
        self._registration_probe = registration_probe
        self._mode_publisher_probe = mode_publisher_probe
        self._lock = RLock()
        self._subscriptions: list[Any] = []
        self._state = "stopped"
        self._error_code: str | None = None
        self._master_uri: str | None = None
        self._node_name: str | None = None

    def status(self) -> dict[str, str | None]:
        with self._lock:
            state = self._state
            error_code = self._error_code
            master_uri = self._master_uri
            node_name = self._node_name
        result = {"state": state, "error_code": error_code}
        if state != "ready":
            return result
        current_uri = str(self._master_uri_getter())
        if current_uri != master_uri:
            return {**result, "state": "not_ready", "error_code": "ros_master_changed"}
        if not self._master_probe():
            return {**result, "state": "not_ready", "error_code": "ros_master_unavailable"}
        if node_name is None or not self._registration_probe(
            current_uri, node_name, REQUIRED_TOPICS
        ):
            return {**result, "state": "not_ready", "error_code": "ros_node_unregistered"}
        if not self._mode_publisher_probe(current_uri, node_name):
            return {**result, "state": "not_ready", "error_code": "handover_publisher_unavailable"}
        return result

    def _set_not_ready(self, code: str) -> None:
        with self._lock:
            self._state = "not_ready"
            self._error_code = code

    @staticmethod
    def _node_is_initialized(rospy: Any) -> bool:
        core = getattr(rospy, "core", None)
        is_initialized = getattr(core, "is_initialized", None)
        return bool(is_initialized()) if callable(is_initialized) else False

    def start(self) -> None:
        with self._lock:
            if self._state == "ready":
                return
        created: list[Any] = []
        try:
            bindings = self._bindings_loader()
            if not self._master_probe():
                self._set_not_ready("ros_master_unavailable")
                return
            if not self._node_is_initialized(bindings.rospy):
                bindings.rospy.init_node(
                    "capture_core_recorder", anonymous=False, disable_signals=True
                )
            get_name = getattr(bindings.rospy, "get_name", None)
            node_name = str(get_name()) if callable(get_name) else "/capture_core_recorder"
            master_uri = str(self._master_uri_getter())
            image_bridge = bindings.cv_bridge_factory()
            for key, topic in REQUIRED_TOPICS.items():
                message_type = self._message_type(bindings, key)
                callback = self._callback(key, image_bridge)
                created.append(
                    bindings.rospy.Subscriber(
                        topic, message_type, callback, queue_size=1
                    )
                )
        except ModuleNotFoundError:
            self._unregister(created)
            self._set_not_ready("ros_unavailable")
            return
        except Exception:  # noqa: BLE001 - isolate ROS startup failures from the API
            self._unregister(created)
            self._set_not_ready("ros_subscriber_error")
            return
        with self._lock:
            self._subscriptions = created
            self._master_uri = master_uri
            self._node_name = node_name
            self._state = "ready"
            self._error_code = None

    @staticmethod
    def _message_type(bindings: RosBindings, key: str) -> Any:
        if key in CAMERA_KEYS:
            return bindings.image_type
        if key in _JOINT_KEYS:
            return bindings.joint_state_type
        if key in _TEXT_KEYS:
            return bindings.string_type
        if key in _BOOL_KEYS:
            return bindings.bool_type
        raise KeyError(key)

    def _callback(self, key: str, image_bridge: Any) -> Any:
        def receive(message: object) -> None:
            arrival = float(self._monotonic())
            try:
                value = self._convert(key, message, image_bridge)
                source = self._source_stamp(message, arrival)
                self._cache.put(key, value, source_stamp=source, arrival_stamp=arrival)
            except Exception:  # noqa: BLE001 - callback must never escape into rospy
                self._set_not_ready("ros_callback_error")

        return receive

    @staticmethod
    def _convert(key: str, message: object, image_bridge: Any) -> object:
        if key in CAMERA_KEYS:
            image = np.asarray(
                image_bridge.imgmsg_to_cv2(message, desired_encoding="bgr8")
            )
            if image.ndim != 3 or image.shape[-1] != 3 or image.dtype != np.uint8:
                raise ValueError("camera callback did not produce BGR uint8")
            # FrameSampler performs the single BGR -> RGB conversion.
            return np.array(image, copy=True)
        if key in _JOINT_KEYS:
            payload: dict[str, np.ndarray] = {}
            for field in ("position", "velocity", "effort"):
                values = np.asarray(
                    getattr(message, field, ()), dtype=np.float32
                ).reshape(-1)
                payload[field] = np.array(values, copy=True)
            if payload["position"].shape != (7,):
                raise ValueError("joint position must contain seven values")
            return payload
        data = message.data  # type: ignore[attr-defined]
        if key in _TEXT_KEYS:
            if not isinstance(data, str):
                raise TypeError("text payload must be a string")
            return data
        if key in _BOOL_KEYS:
            if not isinstance(data, bool):
                raise TypeError("boolean payload must be bool")
            return data
        raise KeyError(key)

    @staticmethod
    def _source_stamp(message: object, arrival: float) -> float:
        header = getattr(message, "header", None)
        stamp = getattr(header, "stamp", None)
        to_seconds = getattr(stamp, "to_sec", None)
        if callable(to_seconds):
            try:
                source = float(to_seconds())
            except (TypeError, ValueError, OverflowError):
                return arrival
            if math.isfinite(source):
                return source
        return arrival

    @staticmethod
    def _unregister(subscriptions: list[Any]) -> None:
        for subscription in reversed(subscriptions):
            try:
                subscription.unregister()
            except Exception:  # noqa: BLE001,S110 - best-effort ROS handle cleanup
                pass

    def shutdown(self) -> None:
        with self._lock:
            subscriptions = self._subscriptions
            self._subscriptions = []
            self._state = "stopped"
            self._master_uri = None
            self._node_name = None
        self._unregister(subscriptions)
