"""Bounded command-line bridge to the existing pause service; no publishers."""
import json
import sys
import rospy
from std_srvs.srv import SetBool, Trigger
from std_msgs.msg import String

rospy.init_node("cobot_deployment_command", anonymous=True, disable_signals=True)
rospy.wait_for_service("/task2/policy/set_paused", timeout=3)
if "--arm" in sys.argv and sys.argv[1] != "pause":
    mode = rospy.wait_for_message("/task2/teach_handover/mode", String, timeout=3)
    if mode.data != "policy":
        print(json.dumps({"success": False, "message": "Finish teaching before starting inference"}))
        raise SystemExit(1)
    rospy.wait_for_service("/task2/policy/arm", timeout=3)
    armed = rospy.ServiceProxy("/task2/policy/arm", Trigger)()
    if not armed.success:
        print(json.dumps({"success": False, "message": armed.message}))
        raise SystemExit(1)
result = rospy.ServiceProxy("/task2/policy/set_paused", SetBool)(sys.argv[1] == "pause")
print(json.dumps({"success": bool(result.success), "message": result.message}))
raise SystemExit(0 if result.success else 1)
