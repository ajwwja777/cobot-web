#!/usr/bin/env python3
"""Single-key M/S client for the Task2 physical-teach handover coordinator.

M and S block on the coordinator while it waits for the operator to press both
rear teach buttons, so each request runs on its own thread and the prompt topic
keeps printing meanwhile.  This client never publishes arm commands; it can
only call the coordinator's services.
"""

import sys
import termios
import threading
import tty

import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger


MANUAL_SERVICE = "/task2/teach_handover/request_manual"
POLICY_SERVICE = "/task2/teach_handover/request_policy"

BANNER = """
Task2 physical-teach handover
  M/m  press M FIRST, then press each rear teach button once to enter teaching
  S/s  press each rear teach button once to LEAVE teaching FIRST, then press S
  Q/q  quit this keyboard only (arms and coordinator keep their state)
"""


class KeyboardClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        rospy.wait_for_service(MANUAL_SERVICE, timeout=30.0)
        rospy.wait_for_service(POLICY_SERVICE, timeout=30.0)
        self.manual = rospy.ServiceProxy(MANUAL_SERVICE, Trigger)
        self.policy = rospy.ServiceProxy(POLICY_SERVICE, Trigger)
        rospy.Subscriber("/task2/teach_handover/prompt", String, self._prompt, queue_size=1)
        rospy.Subscriber("/task2/teach_handover/fault", String, self._fault, queue_size=1)

    @staticmethod
    def _prompt(message: String) -> None:
        text = str(message.data).strip()
        if text:
            print("  [prompt] {}".format(text))

    @staticmethod
    def _fault(message: String) -> None:
        text = str(message.data).strip()
        if text:
            print("  [FAULT] {}".format(text))

    def _call(self, proxy, label: str) -> None:
        try:
            response = proxy()
            status = "ok" if response.success else "REJECTED"
            print("  [{}] {}: {}".format(label, status, response.message))
        except Exception as exc:  # noqa: BLE001 - operator-facing client
            print("  [{}] service call failed: {}".format(label, exc))
        finally:
            with self._lock:
                self._busy = False

    def request(self, proxy, label: str) -> None:
        with self._lock:
            if self._busy:
                print("  [{}] ignored: a request is already in progress".format(label))
                return
            self._busy = True
        print("  [{}] requested".format(label))
        threading.Thread(target=self._call, args=(proxy, label), daemon=True).start()

    def run(self) -> None:
        print(BANNER)
        stdin = sys.stdin.fileno()
        saved = termios.tcgetattr(stdin)
        try:
            tty.setcbreak(stdin)
            while not rospy.is_shutdown():
                key = sys.stdin.read(1)
                if key in ("m", "M"):
                    self.request(self.manual, "manual")
                elif key in ("s", "S"):
                    self.request(self.policy, "policy")
                elif key in ("q", "Q"):
                    print("  keyboard exit; coordinator and arms keep their state")
                    return
        finally:
            termios.tcsetattr(stdin, termios.TCSADRAIN, saved)


def main() -> None:
    rospy.init_node("task2_teach_handover_keyboard", anonymous=True)
    KeyboardClient().run()


if __name__ == "__main__":
    main()
