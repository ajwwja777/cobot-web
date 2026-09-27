#!/usr/bin/env python3
"""Probe whether one Piper rear arm can cycle slave -> master -> slave -> master
without a power cycle.

This tool exists because firmware S-V1.7-3 documents that MasterSlaveConfig(0xFC)
requires an arm restart when the arm is currently in master mode
(see piper_sdk/demo/V2/piper_set_slave.py).  Task2 handover needs the cycle to
repeat indefinitely, so we test recipe variants for the master -> slave step and
check whether a *second* master switch still produces master control frames.

Safety contract:
  - touches exactly one CAN interface, given by --can
  - never calls EnableArm / JointCtrl / GripperCtrl unless --slave-accept-test
  - every stage waits for the operator to press Enter
  - leaves the arm in slave configuration on exit, including on Ctrl-C

Usage (arm powered, operator holding it, e-stop within reach):

    python3 task2_probe_master_slave_cycle.py --can can_rear_left --variant all
"""

import argparse
import socket
import struct
import sys
import threading
import time
from collections import Counter

from piper_sdk import C_PiperInterface_V2

# Frames a master-mode arm broadcasts (joint/gripper control IDs).
MASTER_CTRL_IDS = (0x155, 0x156, 0x157, 0x158, 0x159)
# Frames a slave-mode arm broadcasts (status/joint/end-pose feedback IDs).
SLAVE_FEEDBACK_IDS = (0x2A1, 0x2A2, 0x2A3, 0x2A4, 0x2A5, 0x2A6, 0x2A7, 0x2A8)

MASTER_CONFIG = 0xFA
SLAVE_CONFIG = 0xFC

CAN_FRAME_FMT = "=IB3x8s"
CAN_FRAME_SIZE = 16


class CanSniffer:
    """Count raw CAN arbitration IDs on an interface, independently of the SDK."""

    def __init__(self, ifname):
        self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._sock.bind((ifname,))
        self._sock.settimeout(0.2)
        self._counts = Counter()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                frame = self._sock.recv(CAN_FRAME_SIZE)
            except socket.timeout:
                continue
            except OSError:
                return
            if len(frame) < CAN_FRAME_SIZE:
                continue
            can_id = struct.unpack(CAN_FRAME_FMT, frame)[0] & socket.CAN_EFF_MASK
            with self._lock:
                self._counts[can_id] += 1

    def observe(self, seconds):
        """Reset counters, listen for `seconds`, return {can_id: count}."""
        with self._lock:
            self._counts.clear()
        time.sleep(seconds)
        with self._lock:
            return dict(self._counts)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.0)
        self._sock.close()


def summarize(counts):
    master = {i: counts.get(i, 0) for i in MASTER_CTRL_IDS if counts.get(i, 0)}
    slave = {i: counts.get(i, 0) for i in SLAVE_FEEDBACK_IDS if counts.get(i, 0)}
    other = {i: c for i, c in counts.items()
             if i not in MASTER_CTRL_IDS and i not in SLAVE_FEEDBACK_IDS}
    return master, slave, other


def fmt_ids(d):
    if not d:
        return "-"
    return " ".join("0x%03X:%d" % (i, c) for i, c in sorted(d.items()))


def read_ctrl_mode(piper):
    try:
        status = piper.GetArmStatus()
        if float(status.Hz) <= 0.0:
            return "no-feedback"
        return "0x%02X" % int(status.arm_status.ctrl_mode)
    except Exception as exc:  # noqa: BLE001 - diagnostic tool, report anything
        return "err:%s" % exc


def stage(sniffer, piper, label, window):
    counts = sniffer.observe(window)
    master, slave, other = summarize(counts)
    print("  %-28s ctrl_mode=%-11s master=%-28s slave=%s"
          % (label, read_ctrl_mode(piper), fmt_ids(master), fmt_ids(slave)))
    if other:
        print("  %-28s other=%s" % ("", fmt_ids(other)))
    return bool(master), bool(slave)


def confirm(prompt, assume_yes):
    if assume_yes:
        print("  [auto] %s" % prompt)
        return
    try:
        input("  >>> %s (Enter 继续 / Ctrl-C 中止) " % prompt)
    except EOFError:
        raise KeyboardInterrupt


# --- master -> slave recipes -------------------------------------------------
# Each recipe only needs to get the arm back to a state where a subsequent
# MasterSlaveConfig(0xFA) is honoured again.

def recipe_plain(piper, args):
    """Baseline: exactly what the current driver does."""
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)


def recipe_repeat(piper, args):
    """Resend 0x470 several times; the frame may be dropped while in master mode."""
    for _ in range(5):
        piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
        time.sleep(0.1)


def recipe_clear_first(piper, args):
    """Send the documented 'invalid/clear' code 0x00 before writing slave."""
    piper.MasterSlaveConfig(0x00, 0, 0, 0)
    time.sleep(0.2)
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)


def recipe_standby_first(piper, args):
    """Force resume + standby (piper_ctrl_reset.py sequence) before writing slave."""
    piper.MotionCtrl_1(0x02, 0, 0)
    time.sleep(0.1)
    piper.MotionCtrl_2(0x00, 0x00, 0, 0x00)
    time.sleep(0.3)
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)


def recipe_disable_first(piper, args):
    """Drop the motors to a known state before writing slave.

    Leaves the motors disabled; the arm is limp afterwards.  Only run with the
    arm physically supported.
    """
    piper.DisableArm(7)
    time.sleep(0.5)
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)


def recipe_long_settle(piper, args):
    """Write slave, then give the controller much longer to commit it."""
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
    time.sleep(args.settle_seconds)


def recipe_can_bounce(piper, args):
    """Write slave, then drop and restore the SDK CAN socket.

    Tests whether the stuck state lives in the host-side socket rather than in
    arm firmware.  Does not touch the link layer (no `ip link` calls).
    """
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
    time.sleep(0.3)
    piper.DisconnectPort()
    time.sleep(1.0)
    piper.ConnectPort()
    time.sleep(0.5)


def recipe_home_0x191(piper, args):
    """Firmware >= V1.7-4 only: 0x191 mode 0 = 恢复主从臂模式.

    Expected to be a no-op on S-V1.7-3.  Included so the same probe can confirm
    the fix immediately after a firmware upgrade.
    """
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
    time.sleep(0.3)
    try:
        piper.ReqMasterArmMoveToHome(0)
    except Exception as exc:  # noqa: BLE001
        print("  ReqMasterArmMoveToHome unavailable: %s" % exc)
    time.sleep(0.5)


RECIPES = [
    ("a-plain", recipe_plain, False),
    ("b-repeat", recipe_repeat, False),
    ("c-clear-first", recipe_clear_first, False),
    ("d-standby-first", recipe_standby_first, False),
    ("e-disable-first", recipe_disable_first, True),
    ("f-long-settle", recipe_long_settle, False),
    ("g-can-bounce", recipe_can_bounce, False),
    ("h-home-0x191", recipe_home_0x191, False),
]


def run_variant(piper, sniffer, name, recipe, args):
    print("\n=== variant %s ===" % name)
    stage(sniffer, piper, "0 baseline (expect slave)", args.window)

    confirm("切换为主臂 0xFA（臂会进入重力补偿，扶住）", args.yes)
    piper.MasterSlaveConfig(MASTER_CONFIG, 0, 0, 0)
    m1, _ = stage(sniffer, piper, "1 after 0xFA", args.window)

    confirm("按配方切回从臂 0xFC", args.yes)
    recipe(piper, args)
    m2, s2 = stage(sniffer, piper, "2 after 0xFC recipe", args.window)

    confirm("再次切换为主臂 0xFA（关键一步）", args.yes)
    piper.MasterSlaveConfig(MASTER_CONFIG, 0, 0, 0)
    m3, _ = stage(sniffer, piper, "3 after 2nd 0xFA", args.window)

    confirm("收尾：切回从臂", args.yes)
    piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
    stage(sniffer, piper, "4 restored slave", args.window)

    verdict = "PASS" if (m1 and s2 and not m2 and m3) else "FAIL"
    print("  --> %s  (1st master=%s, slave restored=%s, 2nd master=%s)"
          % (verdict, m1, s2 and not m2, m3))
    return verdict == "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--can", default="can_rear_left",
                        help="single CAN interface to probe (default: can_rear_left)")
    parser.add_argument("--variant", default="a-plain",
                        help="recipe name, comma-separated list, or 'all'")
    parser.add_argument("--window", type=float, default=3.0,
                        help="seconds of CAN observation per stage")
    parser.add_argument("--settle-seconds", type=float, default=10.0,
                        help="settle time for the f-long-settle recipe")
    parser.add_argument("--yes", action="store_true",
                        help="skip per-stage Enter prompts (unattended use only)")
    parser.add_argument("--list", action="store_true", help="list recipes and exit")
    args = parser.parse_args()

    if args.list:
        for name, fn, motors in RECIPES:
            print("%-16s %-6s %s" % (name, "MOTORS" if motors else "", (fn.__doc__ or "").strip().splitlines()[0]))
        return 0

    if args.variant == "all":
        selected = list(RECIPES)
    else:
        wanted = [v.strip() for v in args.variant.split(",")]
        by_name = {n: (n, f, m) for n, f, m in RECIPES}
        missing = [w for w in wanted if w not in by_name]
        if missing:
            parser.error("unknown variant(s): %s" % ", ".join(missing))
        selected = [by_name[w] for w in wanted]

    print("probing %s with variants: %s"
          % (args.can, ", ".join(n for n, _, _ in selected)))
    if any(m for _, _, m in selected):
        print("WARNING: 选中的配方包含 DisableArm，机械臂会失力下坠，必须有人扶住。")

    piper = C_PiperInterface_V2(can_name=args.can)
    piper.ConnectPort()
    sniffer = CanSniffer(args.can)
    time.sleep(1.0)

    try:
        fw = piper.GetPiperFirmwareVersion()
        if fw == -0x4AF:
            piper.SearchPiperFirmwareVersion()
            time.sleep(0.5)
            fw = piper.GetPiperFirmwareVersion()
        print("firmware: %s" % fw)
    except Exception as exc:  # noqa: BLE001
        print("firmware: unavailable (%s)" % exc)

    results = {}
    try:
        for name, recipe, _ in selected:
            results[name] = run_variant(piper, sniffer, name, recipe, args)
    except KeyboardInterrupt:
        print("\n中止：正在切回从臂配置 ...")
    finally:
        try:
            piper.MasterSlaveConfig(SLAVE_CONFIG, 0, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        sniffer.close()

    print("\n=== summary ===")
    for name, ok in results.items():
        print("  %-16s %s" % (name, "PASS" if ok else "FAIL"))
    if not any(results.values()):
        print("  没有配方能在不断电的情况下完成 master -> slave -> master 循环。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
