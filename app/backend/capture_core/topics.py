"""Compatibility import for the control-owned ROS feedback contract."""
from cobot_console.control_import import control_package
control_package()
from cobot_control.ros_topics import *
