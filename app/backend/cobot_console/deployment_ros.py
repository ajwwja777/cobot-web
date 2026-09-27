"""Bounded command-line bridge to the existing pause service; no publishers."""
import json
import sys
import rospy
from std_srvs.srv import SetBool

rospy.init_node("cobot_deployment_command", anonymous=True, disable_signals=True)
rospy.wait_for_service("/task2/policy/set_paused", timeout=3)
result = rospy.ServiceProxy("/task2/policy/set_paused", SetBool)(sys.argv[1] == "pause")
print(json.dumps({"success": bool(result.success), "message": result.message}))
raise SystemExit(0 if result.success else 1)
