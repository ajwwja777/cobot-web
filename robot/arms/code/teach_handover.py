#!/usr/bin/env python3
"""Platform overlay: pause immediately, anchor teleop after a fixed 0.2-second entry interval."""
import importlib.util
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[3] / "integrations/legacy_control"
# Load original helpers and home implementation unchanged.
for sub in ("task2_homing", "task2_handover", "task2_teach_handover"):
    sys.path.insert(0, str(TOOLS / sub))
spec = importlib.util.spec_from_file_location(
    "_cobot_original_button_handover", str(TOOLS / "task2_teach_handover/task2_teach_button_node.py")
)
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


class TeachHandoverNode(original.Task2TeachButtonNode):
    def __init__(self, **kwargs):
        self._entry = {side: None for side in original.SIDES}
        super().__init__(**kwargs)
        self.entry_delay_sec = self._positive_param("~teach_entry_delay_sec", 0.2)

    def _engage(self, side):
        started = self.monotonic_clock()
        super()._engage(side)  # Claims ownership and pauses policy immediately.
        if not self._engaged[side] or self._fault:
            return
        self._hold[side] = self._front_ref[side].positions
        self._entry[side] = {"started": started}
        self.ros.loginfo("示教接管：%s 前臂保持；固定 %.2f 秒后同步，无需后臂静止", side, self.entry_delay_sec)

    def _disengage(self, side):
        self._entry[side] = None
        super()._disengage(side)

    def _forward_once(self):
        with self._lock:
            if self._fault:
                return
            for side in original.SIDES:
                if self._engaged[side]:
                    rear = self._fresh("rear_" + side)
                    if rear is None:
                        started = self._blind_since[side]
                        now = self.monotonic_clock()
                        if started is None:
                            self._blind_since[side] = now
                        elif now - started > self.feedback_gap_fault_sec:
                            self._latch_fault("rear {} feedback went stale for more than {:.1f}s during a takeover".format(side, self.feedback_gap_fault_sec))
                        continue
                    self._blind_since[side] = None
                    entry = self._entry[side]
                    if entry is not None:
                        now = self.monotonic_clock()
                        if now-entry["started"] < self.entry_delay_sec:
                            self._publish_front(side, self._hold[side])
                            continue
                        # Keep the same front hold target; recapture only rear.
                        # First following command is exactly the held front pose.
                        self._rear_ref[side] = rear
                        self._entry[side] = None
                        self.ros.loginfo("示教跟随已就绪：%s；切换阶段位移已排除", side)
                    target = self._clutch_target(side, rear)
                    self._publish_front(side, target)
                    self._hold[side] = target
                elif self.any_engaged and self._hold[side] is not None:
                    self._publish_front(side, self._hold[side])


def main():
    original.rospy.init_node("task2_teach_button_handover", anonymous=False)
    TeachHandoverNode().spin()


if __name__ == "__main__":
    main()
