#!/usr/bin/env python3
"""Foreground single-key client for the Task2 handover coordinator."""

from contextlib import contextmanager
import sys
import termios
import tty
from typing import Callable, Optional, TextIO

import rospy
from std_srvs.srv import Trigger


MANUAL_SERVICE = "/task2/handover/request_manual"
POLICY_SERVICE = "/task2/handover/request_policy"


def command_for_key(key: str) -> Optional[str]:
    if not isinstance(key, str) or len(key) != 1:
        return None
    return {"m": "manual", "s": "policy", "q": "quit"}.get(key.lower())


def _emit(output: TextIO, message: str) -> None:
    output.write(message + "\n")
    flush = getattr(output, "flush", None)
    if flush is not None:
        flush()


def run_keyboard(
    read_key: Callable[[], str],
    manual_call: Callable,
    policy_call: Callable,
    output: TextIO = sys.stdout,
) -> None:
    """Dispatch single characters until Q; never retries a failed request."""
    _emit(output, "Task2 handover: M=manual, S=policy, Q=quit keyboard")
    while True:
        key = read_key()
        command = command_for_key(key)
        if command is None:
            continue
        if command == "quit":
            _emit(output, "Keyboard client exited; robot mode was not changed.")
            return
        call = manual_call if command == "manual" else policy_call
        try:
            response = call()
            status = "OK" if bool(getattr(response, "success", False)) else "FAILED"
            _emit(
                output,
                "{} {}: {}".format(
                    status,
                    command,
                    getattr(response, "message", "no response message"),
                ),
            )
        except Exception as exc:
            _emit(output, "FAILED {}: {}".format(command, exc))


@contextmanager
def terminal_cbreak(stream=sys.stdin):
    """Enter cbreak mode and unconditionally restore the terminal settings."""
    descriptor = stream.fileno()
    original = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield lambda: stream.read(1)
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)


def main() -> None:
    rospy.init_node("task2_handover_keyboard", anonymous=False)
    rospy.wait_for_service(MANUAL_SERVICE)
    rospy.wait_for_service(POLICY_SERVICE)
    manual_call = rospy.ServiceProxy(MANUAL_SERVICE, Trigger)
    policy_call = rospy.ServiceProxy(POLICY_SERVICE, Trigger)
    with terminal_cbreak() as read_key:
        run_keyboard(read_key, manual_call, policy_call)


if __name__ == "__main__":
    main()
