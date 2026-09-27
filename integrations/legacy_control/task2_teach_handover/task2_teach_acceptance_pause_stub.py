#!/usr/bin/env python3
"""Acceptance-only stub for /task2/policy/set_paused.

The coordinator refuses to hand control back to a policy it cannot confirm is
paused.  That check is correct and must stay, but it means the hardware
acceptance stages that deliberately run WITHOUT a model have nothing to answer
it.  This node answers it, and does nothing else.

NOT FOR PRODUCTION.  It publishes no topics at all - in particular it never
publishes /task2/policy/joint_left or /task2/policy/joint_right - so it cannot
move any arm.  Run the real policy adapter for stages 7 and 8; if this stub is
running at the same time as a real policy, the real one loses its service name
and the run is invalid.
"""

import rospy
from std_srvs.srv import SetBool, SetBoolResponse


SERVICE = "/task2/policy/set_paused"


class AcceptancePauseStub:
    def __init__(self) -> None:
        self.paused = True
        self.calls = []
        self.service = rospy.Service(SERVICE, SetBool, self.handle_set_paused)
        rospy.logwarn(
            "Task2 ACCEPTANCE pause stub is serving %s. It answers the "
            "coordinator and publishes nothing. Do not use with a real policy.",
            SERVICE,
        )

    def handle_set_paused(self, request) -> SetBoolResponse:
        self.paused = bool(request.data)
        self.calls.append(self.paused)
        rospy.loginfo(
            "Task2 acceptance pause stub: paused=%s (call #%d)",
            self.paused,
            len(self.calls),
        )
        return SetBoolResponse(
            success=True,
            message="acceptance stub: paused={}".format(self.paused),
        )


def main() -> None:
    rospy.init_node("task2_teach_acceptance_pause_stub", anonymous=False)
    AcceptancePauseStub()
    rospy.spin()


if __name__ == "__main__":
    main()
