#!/usr/bin/env python3
"""Observe ROS and CAN feedback only; never publish a command or call a service."""
import argparse
import json
import sys
import time
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app/backend"))
from cobot_console.paths import configure_environment
configure_environment()
from cobot_console.device_control import _default_system_probe
from cobot_console.device_health import DeviceHealth
from capture_core.ros_cache import LatestMessageCache
from capture_core.ros_subscriber import RosSubscriberBridge, _load_ros_bindings
from capture_core.topics import REQUIRED_TOPICS, CAMERA_KEYS

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=float,default=1.5)
    args=parser.parse_args()
    if not .5 <= args.seconds <= 10:
        parser.error("--seconds must be between 0.5 and 10")
    bindings=_load_ros_bindings()
    bindings.rospy.init_node("cobot_web_passive_health_check",anonymous=True,disable_signals=True)
    cache=LatestMessageCache();bridge=RosSubscriberBridge(cache)
    subscribers=[]
    try:
        for key,topic in REQUIRED_TOPICS.items():
            if key in CAMERA_KEYS:
                continue
            subscribers.append(bindings.rospy.Subscriber(topic,bridge._message_type(bindings,key),
                bridge._callback(key,None),queue_size=1))
        time.sleep(args.seconds)
        systems=_default_system_probe()
        result=DeviceHealth().evaluate(systems,cache.snapshot(time.monotonic()))
        print(json.dumps({"control_routes":systems.get("control_routes"),"health":result},ensure_ascii=False,indent=2))
    finally:
        for subscriber in subscribers:subscriber.unregister()
        bindings.rospy.signal_shutdown("Passive observation complete")

if __name__=="__main__":
    main()
