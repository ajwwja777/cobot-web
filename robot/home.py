#!/usr/bin/env python3
"""一键把机械臂送回统一起点。

    task2_home_cli.py front            前双臂回起点（它们一直在 CAN 控制，不失能不掉落）
    task2_home_cli.py rear             后双臂回起点（从重力位使能 → 进 CAN → 静止 1.2s → 上去）
    task2_home_cli.py all              前双臂、左右后臂并行归位
    home.sh capture                    保存前双臂实测位姿
    home.sh capture --arm mid          仅保存中臂实测位姿
    home.sh mid --pose <name>          仅移动中臂到保存位姿
    home.sh show --arm mid --pose <name>  查看中臂偏差
    task2_home_cli.py show             打印当前配置和实测位姿的差值

    --pose <name>                      用别的命名位姿（默认 collect_start）
    --config <path>                    用别的配置文件

设计上的两点，改之前先读：

  · 位姿目标通过 ROS 参数传给节点，不是通过服务请求。std_srvs 里没有能装位姿
    的消息，自定义 srv 就得重新编译 piper 包。本脚本在调用服务之前把参数写好。

  · 真正动手臂的是两个节点，不是这个脚本。前臂归协调器管（它是 /master/joint_*
    的唯一发布者），后臂归后臂驱动管（它持有 CAN 句柄，并且在退出示教时会发
    DisableArm）。另起进程去抢这两样东西早晚出事。
"""

import argparse
import fcntl
import tempfile
import os
import re
import sys
import threading
import time

import rospy
import yaml
from sensor_msgs.msg import JointState
from std_srvs.srv import Trigger

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task2_homing_core as homing  # noqa: E402


DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "home_poses.yaml")
FRONT_FEEDBACK = {"left": "/puppet/joint_left", "right": "/puppet/joint_right"}
REAR_FEEDBACK = {
    "left": "/task2/teach/rear_left/joint_states",
    "right": "/task2/teach/rear_right/joint_states",
}
FRONT_HOME_SERVICE = "/task2/teach_handover/home_front"
REAR_HOME_SERVICES = {
    "left": "/task2/teach/rear_left/home",
    "right": "/task2/teach/rear_right/home",
}
POSE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SELECTABLE_ARMS = ("front-left", "front-right", "mid", "rear-left", "rear-right")


def parse_targets(value):
    items = value.split(",") if isinstance(value, str) else []
    if not items or len(items) > len(SELECTABLE_ARMS) or len(set(items)) != len(items):
        raise homing.HomingError("select one or more distinct arms")
    if any(item not in SELECTABLE_ARMS for item in items):
        raise homing.HomingError("unknown arm in selection")
    return tuple(item for item in SELECTABLE_ARMS if item in items)


def resolve_selected(config, pose_name, targets):
    entry = (config.get("poses") or {}).get(pose_name) if isinstance(config, dict) else None
    if not isinstance(entry, dict):
        raise homing.HomingError("unknown pose {!r}".format(pose_name))
    resolved = {}
    for arm in targets:
        key = arm.replace("-", "_")
        source = key
        if arm.startswith("rear-") and source not in entry:
            source = "front_" + arm.split("-", 1)[1]
        if source not in entry:
            raise homing.HomingError("pose {!r} is missing {}".format(pose_name, key))
        resolved[key] = homing.validate_pose(entry[source], key)
    return resolved


def load_config(path):
    with open(path, "r") as handle:
        return yaml.safe_load(handle) or {}


def measured(topic, timeout=5.0):
    message = rospy.wait_for_message(topic, JointState, timeout=timeout)
    return homing.validate_pose(list(message.position)[:7], topic)


def push_params(resolved, config):
    joint_speed, gripper_speed, rate = homing.speed_settings(config)
    rospy.set_param(
        "/task2/homing/speed",
        {
            "joint_rad_per_sec": joint_speed,
            "gripper_m_per_sec": gripper_speed,
            "publish_rate_hz": rate,
        },
    )
    for key, values in resolved.items():
        rospy.set_param("/task2/homing/" + key, list(values))


def call(service, label=None):
    try:
        rospy.wait_for_service(service, timeout=5.0)
    except rospy.ROSException:
        print("服务不可用: {}".format(service))
        return False
    try:
        response = rospy.ServiceProxy(service, Trigger)()
    except rospy.ServiceException as exc:
        print("{} 调用失败: {}".format(service, exc))
        return False
    status = "OK  " if response.success else "失败"
    print("{} {}: {}".format(status, label or service, response.message))
    return bool(response.success)


def do_front(resolved, config):
    push_params(resolved, config)
    print("前臂归位中（一直在 CAN 控制，不会失能、不会掉落）...")
    if not call(FRONT_HOME_SERVICE):
        return False
    return verify_front_arrival(resolved)


def verify_front_arrival(resolved):
    # The legacy service reports published trajectory, not measured arrival.
    deadline = time.monotonic() + 3.0
    last_errors = {}
    while time.monotonic() < deadline:
        try:
            last_errors = {}
            for side in ("left", "right"):
                now = measured(FRONT_FEEDBACK[side], timeout=1.0)
                target = resolved["front_" + side]
                last_errors[side] = max(abs(a-b) for a,b in zip(now[:6], target[:6]))
            if all(value <= 0.03 for value in last_errors.values()):
                print("前双臂实测关节已到位（容差0.03 rad；夹爪未纳入此检查）。")
                return True
        except Exception as exc:
            print("归位实测反馈不可用: {}".format(exc))
        time.sleep(0.1)
    print("归位未到位: {}；服务成功仅代表指令已发布，不代表实际到位。".format(last_errors))
    return False


def do_rear(resolved, config):
    """两条后臂并行归位。

    它们是两个独立的驱动进程、两条独立的 CAN 总线，本来就能同时动。
    串行调用会让总时间翻倍，而且看起来像"左臂先、右臂后"——那是 CLI
    在一条一条地等，不是机械臂的限制。
    """
    push_params(resolved, config)
    print("后臂归位中（从重力位使能 → 进 CAN 控制 → 静止等故障过去 → 移动）...")
    results = {}
    threads = []
    for side in ("left", "right"):
        def run(side=side):
            results[side] = call(REAR_HOME_SERVICES[side])

        thread = threading.Thread(target=run, name="home-" + side)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    ok = all(results.get(side) for side in ("left", "right"))
    if ok:
        print("后臂已停在起点保持，可以按示教按钮了。")
    return ok



def do_all(resolved, config):
    """Preflight every endpoint, then request all three owners concurrently.

    This removes CLI sequencing. Legacy rear enable/settle preparation remains
    inside each driver; a CLI barrier is not a shared physical trajectory clock.
    """
    services = {"front": FRONT_HOME_SERVICE,
                "rear_left": REAR_HOME_SERVICES["left"],
                "rear_right": REAR_HOME_SERVICES["right"]}
    proxies = {}
    try:
        for name, service in services.items():
            rospy.wait_for_service(service, timeout=5.0)
            proxies[name] = rospy.ServiceProxy(service, Trigger)
    except (rospy.ROSException, rospy.ServiceException) as exc:
        print("归位服务预检失败，本次未发出归位请求: {}".format(exc))
        return False
    push_params(resolved, config)
    print("前双臂、左右后臂并行归位；后臂保留驱动内部使能/稳定准备。")
    start = threading.Barrier(len(services) + 1)
    results = {}
    def run(name):
        try:
            start.wait()
            response = proxies[name]()
            print("{} {}: {}".format("OK  " if response.success else "失败",
                                     services[name], response.message))
            ok = bool(response.success)
            if name == "front" and ok:
                ok = verify_front_arrival(resolved)
            results[name] = ok
        except threading.BrokenBarrierError:
            results[name] = False
        except Exception as exc:
            results[name] = False
            print("{} 归位失败: {}".format(services[name], exc))
    threads = []
    try:
        for name in services:
            thread = threading.Thread(target=run, args=(name,), name="home-" + name)
            thread.start()
            threads.append(thread)
    except Exception as exc:
        start.abort()
        for thread in threads:
            thread.join()
        print("归位线程准备失败，本次未发出归位请求: {}".format(exc))
        return False
    start.wait()
    for thread in threads:
        thread.join()
    ok = all(results.get(name, False) for name in services)
    print("四臂归位完成。" if ok else "并行归位未全部成功，请查看各臂结果。")
    return ok


def do_selected(resolved, config, targets):
    """Move only selected arms; the shared front service holds its other arm."""
    selected = set(targets)
    front = any(arm.startswith("front-") for arm in selected)
    services = {}
    try:
        if front:
            for side in ("left", "right"):
                key = "front_" + side
                if "front-" + side not in selected:
                    resolved[key] = measured(FRONT_FEEDBACK[side])
            rospy.wait_for_service(FRONT_HOME_SERVICE, timeout=5.0)
            services["front"] = rospy.ServiceProxy(FRONT_HOME_SERVICE, Trigger)
        for side in ("left", "right"):
            if "rear-" + side in selected:
                service = REAR_HOME_SERVICES[side]
                rospy.wait_for_service(service, timeout=5.0)
                services["rear_" + side] = rospy.ServiceProxy(service, Trigger)
        if "mid" in selected:
            import rosgraph
            import mid_home
            mid_home.check_publishers(rosgraph.Master(rospy.get_name()))
            mid_home.check_session()
    except Exception as exc:
        print("Selected home preflight failed; no movement requested: {}".format(exc))
        return False
    if services:
        push_params({key: value for key, value in resolved.items() if key != "mid"}, config)
    participants = list(services) + (["mid"] if "mid" in selected else [])
    barrier = threading.Barrier(len(participants) + 1)
    results = {}

    def run(part):
        try:
            barrier.wait()
            if part == "mid":
                import mid_home
                results[part] = bool(mid_home.move(resolved["mid"], config, assume_yes=True))
            else:
                response = services[part]()
                ok = bool(response.success)
                if part == "front" and ok:
                    ok = verify_front_arrival(resolved)
                results[part] = ok
                print("{} {}: {}".format("OK" if ok else "FAILED", part, response.message))
        except Exception as exc:
            results[part] = False
            print("Selected home {} failed: {}".format(part, exc))

    threads = [threading.Thread(target=run, args=(part,), name="home-"+part) for part in participants]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    return all(results.get(part, False) for part in participants)


def do_capture(args, config):
    """Capture the selected source pose; write atomically after all reads.

    ``--arm all`` deliberately captures only the front pair.  During ``home
    all`` each rear arm reuses its matching front target, so a four-arm home
    is derived from one authoritative paired pose instead of four readings
    that may have been sampled at slightly different instants.
    """
    from mid_home import FEEDBACK as MID_FEEDBACK
    if getattr(args, "targets", None):
        sources = {"front-left": FRONT_FEEDBACK["left"], "front-right": FRONT_FEEDBACK["right"],
                   "mid": MID_FEEDBACK, "rear-left": REAR_FEEDBACK["left"],
                   "rear-right": REAR_FEEDBACK["right"]}
        topics = {arm.replace("-", "_"): sources[arm] for arm in parse_targets(args.targets)}
    elif args.arm == "mid":
        topics = {"mid": MID_FEEDBACK}
    elif args.arm == "rear":
        topics = {"rear_" + side: REAR_FEEDBACK[side] for side in ("left", "right")}
    else:
        topics = {"front_" + side: FRONT_FEEDBACK[side] for side in ("left", "right")}
    if args.include_rear:
        topics.update({"rear_" + side: REAR_FEEDBACK[side] for side in ("left", "right")})
    captured = {key: [round(v, 6) for v in measured(topic)] for key, topic in topics.items()}
    path = os.path.abspath(args.config)
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fresh = load_config(path)
        if not isinstance(fresh, dict):
            raise homing.HomingError("home config must be a mapping")
        entry = fresh.setdefault("poses", {}).setdefault(args.pose, {})
        if args.arm == "all" and not getattr(args, "targets", None):
            # Remove explicit rear overrides from an older capture.  Leaving
            # them behind would make home all ignore the newly captured front
            # targets and move the rear pair to stale positions.
            entry.pop("rear_left", None)
            entry.pop("rear_right", None)
        entry.update(captured)
        with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(path),
                                         prefix=".home-poses-", delete=False) as handle:
            yaml.safe_dump(fresh, handle, default_flow_style=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, os.stat(path).st_mode & 0o777)
        os.replace(handle.name, path)
    if args.arm == "mid":
        label = "中臂"
    elif args.arm == "all":
        label = "前双臂（home all 时后双臂复用对应目标）"
    else:
        label = "所选臂"
    print("已把{}当前位姿存为 {!r} → {}".format(label, args.pose, path))
    for key, value in sorted(captured.items()):
        print("  {:<12} {}".format(key, [round(v, 4) for v in value]))
    return True


def do_delete(args):
    path = os.path.abspath(args.config)
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("删除 pose 需要现场终端确认，或由网页二次确认后使用 --yes")
        answer = input("删除命名 pose {!r} 的全部机械臂记录；输入 Enter 确认，其他内容取消：".format(args.pose))
        if answer.strip():
            print("已取消。")
            return False
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        fresh = load_config(path)
        poses = fresh.get("poses") if isinstance(fresh, dict) else None
        if not isinstance(poses, dict) or args.pose not in poses:
            raise homing.HomingError("unknown pose {!r}".format(args.pose))
        del poses[args.pose]
        with tempfile.NamedTemporaryFile(mode="w", dir=os.path.dirname(path),
                                         prefix=".home-poses-", delete=False) as handle:
            yaml.safe_dump(fresh, handle, default_flow_style=False, allow_unicode=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, os.stat(path).st_mode & 0o777)
        os.replace(handle.name, path)
    print("已删除命名 pose {!r} 的全部机械臂记录 → {}".format(args.pose, path))
    return True


def resolve_for_action(config, pose_name, action):
    if action in ("all", "show"):
        return homing.resolve_pose_set(config, pose_name)
    poses = config.get("poses") if isinstance(config, dict) else None
    if not isinstance(poses, dict) or pose_name not in poses:
        raise homing.HomingError("unknown pose {!r}".format(pose_name))
    entry = poses[pose_name]
    if not isinstance(entry, dict):
        raise homing.HomingError("pose {!r} must be a mapping".format(pose_name))
    resolved = {}
    for side in ("left", "right"):
        key = action + "_" + side
        source = key
        if action == "rear" and source not in entry:
            source = "front_" + side
        if source not in entry:
            raise homing.HomingError("pose {!r} is missing {}".format(pose_name, key))
        resolved[key] = homing.validate_pose(entry[source], key)
    return resolved


def do_show(resolved):
    """当前位姿离起点还有多远。

    表头故意全用 ASCII：中文字符在终端里占两列，而 str.format 的宽度按一列算，
    混在一起表格必歪。
    """
    topics = [
        ("front_left", FRONT_FEEDBACK["left"]),
        ("front_right", FRONT_FEEDBACK["right"]),
        ("rear_left", REAR_FEEDBACK["left"]),
        ("rear_right", REAR_FEEDBACK["right"]),
    ]
    print("当前位姿与起点的偏差（关节 rad，夹爪 m）:")
    print("  {:<12}{:>10}  {}".format("arm", "max", "joint"))
    for name, topic in topics:
        try:
            now = measured(topic, timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            print("  {:<12}{:>10}  读不到 ({})".format(name, "-", exc))
            continue
        deltas = [abs(a - b) for a, b in zip(now, resolved[name])]
        worst = max(range(homing.JOINT_COUNT), key=lambda index: deltas[index])
        print("  {:<12}{:>10.4f}  j{}".format(name, deltas[worst], worst + 1))
    print("  (后臂失能躺在重力位时偏差大是正常的)")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("front", "rear", "all", "selected", "capture", "delete", "show", "gripper", "mid"))
    parser.add_argument("--targets", help="Comma-separated explicit arm selection for selected/capture")
    parser.add_argument("--arm", choices=("front", "rear", "all", "mid"), default="front",
                        help="capture/show 时选择臂；默认前双臂")
    parser.add_argument("--pose", default="collect_start")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--yes", action="store_true",
                        help="跳过终端 Enter；仅供已有二次确认的网页固定入口使用")
    parser.add_argument(
        "--include-rear",
        action="store_true",
        help="capture 时把后臂位姿也单独存下来（默认后臂跟随前臂）",
    )
    args = parser.parse_args()

    if bool(args.targets) != (args.action == "selected") and not (args.action == "capture" and args.targets):
        parser.error("--targets is required for selected and only accepted for selected/capture")
    if args.targets:
        try:
            parse_targets(args.targets)
        except homing.HomingError as exc:
            parser.error(str(exc))

    if not POSE_NAME_RE.fullmatch(args.pose):
        parser.error("pose 名称只允许字母、数字、下划线和连字符，长度 1–64")
    if args.arm == "mid" and args.action not in ("capture", "show", "mid"):
        parser.error("--arm mid 用于 capture/show；移动请用 home.sh mid --pose 名称")
    if args.arm in ("mid", "rear", "all") and args.include_rear:
        parser.error("--include-rear 只用于兼容旧的 --arm front capture")

    if args.action == "delete":
        return 0 if do_delete(args) else 1

    if args.action == "gripper":
        if args.pose != "reinit":
            parser.error("夹爪操作请使用 home.sh gripper --pose reinit")
        import gripper_cycle
        return gripper_cycle.cli(assume_yes=args.yes)

    rospy.init_node("task2_home_cli", anonymous=True, disable_signals=True)
    config = load_config(args.config)

    if args.action == "capture":
        return 0 if do_capture(args, config) else 1

    if args.action == "selected":
        try:
            targets = parse_targets(args.targets)
            resolved = resolve_selected(config, args.pose, targets)
        except homing.HomingError as exc:
            print("Selected pose invalid: {}".format(exc))
            return 2
        return 0 if do_selected(resolved, config, targets) else 1

    if args.action == "mid" or (args.action == "show" and args.arm == "mid"):
        import mid_home
        target = mid_home.resolve_target(config, args.pose)
        if args.action == "show":
            now = measured(mid_home.FEEDBACK)
            print("中臂六关节最大偏差: {:.6f} rad".format(max(abs(a-b) for a,b in zip(now[:6], target[:6]))))
            return 0
        return 0 if mid_home.move(target, config, assume_yes=args.yes) else 1

    try:
        resolved = resolve_for_action(config, args.pose, args.action)
    except homing.HomingError as exc:
        print("配置有问题: {}".format(exc))
        print("先用 `task2_home_cli.py capture` 把当前位姿存成起点。")
        return 2

    if args.action == "show":
        return 0 if do_show(resolved) else 1
    if args.action == "front":
        return 0 if do_front(resolved, config) else 1
    if args.action == "rear":
        return 0 if do_rear(resolved, config) else 1
    return 0 if do_all(resolved, config) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中止；不继续发布移动指令。")
        sys.exit(130)
    except (homing.HomingError, RuntimeError, rospy.ROSException) as exc:
        print("操作未完成: {}".format(exc))
        sys.exit(1)
