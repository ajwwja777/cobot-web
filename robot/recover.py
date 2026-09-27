#!/usr/bin/env python3
"""统一现场恢复入口；选定一条臂，不归位，不自动恢复rollout。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import time
import sys
import rospy
from std_srvs.srv import Trigger, SetBool

SERVICES = {
 "rear-left": "/task2/teach/rear_left/recover_idle",
 "rear-right": "/task2/teach/rear_right/recover_idle",
 "front-left": "/task2/front/left/recover_can",
 "front-right": "/task2/front/right/recover_can",
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=tuple(SERVICES) + ("front-pair", "mid", "sync", "gripper-left", "gripper-right"))
    parser.add_argument("--supported", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--sudo-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.arm == "sync":
        try:
            from sync_recovery import recover_sync
            recover_sync()
        except (RuntimeError, OSError, rospy.ROSException) as exc:
            print("同步恢复未完成："+str(exc))
            return 1
        print("同步故障已清除；释放两侧后臂示教按钮，再重新按下接管。不会归位或自动恢复自主rollout。")
        return 0
    if not args.supported:
        if not sys.stdin.isatty():
            print("请在现场交互终端执行；恢复先失能，需要事先手动放到最低安全位置并可靠支撑。")
            return 1
        try:
            prompt = ("先手动把所选前臂放到最低安全位置并可靠支撑。停止其他控制、释放后臂示教按钮；按 Enter 一次完成失能和恢复 CAN/使能，取消按 Ctrl-C：" if args.arm.startswith("front-") else "恢复 "+args.arm+" 会短暂失能。停止其他控制、释放示教按钮并可靠支撑后按 Enter；取消按 Ctrl-C：")
            if args.arm == "mid":
                prompt = "先手动把中臂放到最低安全位置并可靠支撑，确认相机/线缆安全并停止其他控制；按 Enter 一次完成失能和恢复 CAN/使能，取消按 Ctrl-C："
            if args.arm.startswith("gripper-"):
                prompt = "夹爪将保持当前开度，只执行一次清错和原位使能；不会设零点或开合。停止其他控制、移开手和物体后按 Enter，取消按 Ctrl-C："
            answer=input(prompt)
            if answer.strip():
                print("已取消。")
                return 1
        except (EOFError,KeyboardInterrupt):
            print("\n已取消。")
            return 1
    if args.arm.startswith("gripper-"):
        from gripper_cycle import recover_gripper
        try:
            result=recover_gripper(args.arm.split("-")[1])
        except (RuntimeError,OSError,ValueError,rospy.ROSException) as exc:
            print("夹爪恢复已停止："+str(exc))
            return 1
        print("夹爪恢复成功：{}。未归位、未开合、未设零点。".format(result))
        return 0
    if args.arm.startswith("front-") or args.arm == "mid":
        from front_reset import recover_front, recover_mid
        sudo_password=None
        if args.sudo_stdin:
            sudo_password=sys.stdin.readline().rstrip("\r\n")
            if not sudo_password:
                print("恢复已停止：网页未提供 CAN 自动自愈所需的 sudo 密码")
                return 1
        try:
            recovery_kwargs={} if sudo_password is None else {"sudo_password":sudo_password}
            if args.arm == "mid":
                state = recover_mid(**recovery_kwargs)
            elif args.arm == "front-pair":
                from front_mode import operator_guard
                if not rospy.core.is_initialized():
                    rospy.init_node("platform_front_pair_recovery", anonymous=True, disable_signals=True)
                # Preflight both arms before either is disabled. They use separate CAN buses.
                for side in ("left", "right"):
                    operator_guard(side, allow_coordinator_fault=True)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {side: pool.submit(recover_front, side, **recovery_kwargs)
                               for side in ("left", "right")}
                    results = {}
                    for side, future in futures.items():
                        try:
                            results[side] = future.result()
                        except (RuntimeError, OSError, rospy.ROSException) as exc:
                            results[side] = None
                            print("{}前臂恢复失败：{}".format("左" if side == "left" else "右", exc))
                if any(result is None for result in results.values()):
                    print("前双臂未全部恢复；已完成的一侧不会自动回退。")
                    return 1
                state = results
            else:
                state = recover_front(args.arm.split("-")[1], **recovery_kwargs)
        except (RuntimeError,OSError,rospy.ROSException) as exc:
            print("恢复已停止："+str(exc))
            return 1
        finally:
            sudo_password=None
        if state is None:
            return 1
        print("恢复序列完成：CAN模式，六个关节使能且无故障。夹爪未操作；请现场验证控制或归位。")
        if args.arm.startswith("front-"):
            try:
                from sync_recovery import recover_sync
                recover_sync()
            except (RuntimeError, OSError, rospy.ROSException) as exc:
                print("前臂硬件恢复已完成；同步故障尚未清除："+str(exc)+"。反馈正常后执行 ./scripts/recover.sh sync；无需重复失能恢复。")
                return 1
            print("同步协调器已恢复；重新按后臂示教按钮接管，不会自动恢复自主rollout。")
        return 0
    rospy.init_node("task2_recover_cli", anonymous=True, disable_signals=True)
    service = SERVICES[args.arm]
    rospy.wait_for_service(service, timeout=5.0)
    # When a policy is present it must acknowledge pause before hardware recovery.
    try:
        rospy.wait_for_service("/task2/policy/set_paused", timeout=1.0)
    except rospy.ROSException:
        print("无策略暂停服务：请确认部署Session已结束。")
    else:
        response = rospy.ServiceProxy("/task2/policy/set_paused", SetBool)(True)
        if not response.success:
            print("策略未确认暂停，取消恢复。")
            return 1
    time.sleep(0.4)
    response = rospy.ServiceProxy(service, Trigger)()
    print(("OK: " if response.success else "失败: ") + response.message)
    if not response.success:
        return 1
    rospy.wait_for_service("/task2/teach_handover/reset_fault", timeout=5.0)
    reset = rospy.ServiceProxy("/task2/teach_handover/reset_fault", Trigger)()
    print("协调器: " + reset.message)
    print("恢复后重新按后臂示教按钮接管；不会自动恢复自主rollout。")
    return 0 if reset.success else 1

if __name__ == "__main__":
    raise SystemExit(main())
