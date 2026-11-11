"""Forge saved-map localization lifecycle, backed by ROS2 AMCL."""

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
from dora import Node
from forge_msgs import Pose

from localization.consistency import StationaryConsistencyGuard
from localization.policy import PolicyEndpoint
from localization.process import OwnedProcess
from localization.quality import AgreementGate, planar_pose, scan_agreement
from localization.relocalization import (
    ScanMatcher,
    load_pose_cache,
    map_identity,
    save_pose_cache,
)


def run(cfg: dict) -> None:
    import rclpy
    from geometry_msgs.msg import PoseWithCovarianceStamped
    from lifecycle_msgs.srv import GetState
    from nav2_msgs.srv import ManageLifecycleNodes
    from nav_msgs.msg import OccupancyGrid, Odometry
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from sensor_msgs.msg import LaserScan
    from std_srvs.srv import Empty
    from tf2_ros import Buffer, TransformListener

    rclpy.init(domain_id=cfg["ros_domain_id"])
    ros = rclpy.create_node("forge_amcl_adapter")
    dora = Node()
    endpoint = PolicyEndpoint(dora, "localization")
    backend = OwnedProcess()
    buffer = Buffer()
    _listener = TransformListener(buffer, ros)
    pub = ros.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
    no_motion = ros.create_client(Empty, "/request_nomotion_update")
    lifecycle = ros.create_client(GetState, "/amcl/get_state")
    lifecycle_future, lifecycle_active, lifecycle_checked = None, False, 0.0
    manager = ros.create_client(
        ManageLifecycleNodes, "/lifecycle_manager_localization/manage_nodes"
    )
    manager_future, manager_ready_at = None, None
    state, pending, seed = "idle", None, None
    grid = info = scan = amcl = None
    last_scan = last_odom = last_amcl = last_health = last_update = 0.0
    initialized_at = 0.0
    healthy_count = 0
    no_motion_future = None
    deadline = 0
    previous_pose, jump_latched = None, False
    odom_pose = None
    amcl_odom_pose = None
    consistency = StationaryConsistencyGuard()
    startup_updates_remaining = 3
    initial_message, initial_sent_at, initial_attempts = None, 0.0, 0
    initial_stamp_ns = 0
    gate = AgreementGate(**cfg.get("agreement_gate", {}))
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="map-search")
    search_future = None
    search_epoch = 0
    search_odom = None
    search_started = 0.0
    relocalize_request = None
    localization_mode = "unconfirmed"
    search_details = {}
    trusted = False
    last_cache_save = 0.0
    identity = map_identity(cfg["map_file"])
    cache_file = cfg.get("pose_cache_file", str(Path(cfg["session_dir"]) / "last_pose.json"))

    def on_map(msg):
        nonlocal grid, info
        grid = np.asarray(msg.data, dtype=np.int16).reshape(msg.info.height, msg.info.width)
        p, q = msg.info.origin.position, msg.info.origin.orientation
        info = (msg.info.resolution, [p.x, p.y, 2 * math.atan2(q.z, q.w)])

    def on_scan(msg):
        nonlocal scan, last_scan
        scan, last_scan = msg, time.monotonic()

    def on_odom(msg):
        nonlocal last_odom, odom_pose
        last_odom = time.monotonic()
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        odom_pose = [p.x, p.y, 2 * math.atan2(q.z, q.w)]

    def on_amcl(msg):
        nonlocal amcl, last_amcl, amcl_odom_pose
        stamp = msg.header.stamp.sec * 10**9 + msg.header.stamp.nanosec
        if stamp < initial_stamp_ns:
            return
        amcl, last_amcl = msg, time.monotonic()
        amcl_odom_pose = None if odom_pose is None else list(odom_pose)

    ros.create_subscription(
        OccupancyGrid,
        "/map",
        on_map,
        QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        ),
    )
    ros.create_subscription(
        LaserScan,
        cfg.get("scan_topic", "/scan"),
        on_scan,
        QoSProfile(depth=5, reliability=ReliabilityPolicy.BEST_EFFORT),
    )
    ros.create_subscription(Odometry, cfg.get("odometry_topic", "/odom"), on_odom, 10)
    ros.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", on_amcl, 10)

    def initialize(pose, *, source="manual"):
        nonlocal initialized_at, healthy_count, amcl, last_amcl, previous_pose, jump_latched
        nonlocal consistency, startup_updates_remaining, amcl_odom_pose
        nonlocal initial_message, initial_sent_at, initial_attempts, initial_stamp_ns
        nonlocal gate, trusted, localization_mode
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = ros.get_clock().now().to_msg()
        msg.pose.pose.position.x, msg.pose.pose.position.y, yaw = planar_pose(pose)
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = (
            math.sin(yaw / 2),
            math.cos(yaw / 2),
        )
        msg.pose.covariance[0] = msg.pose.covariance[7] = 0.15**2
        msg.pose.covariance[35] = 0.20**2
        initial_stamp_ns = ros.get_clock().now().nanoseconds
        msg.header.stamp = rclpy.time.Time(nanoseconds=initial_stamp_ns).to_msg()
        pub.publish(msg)
        gate = AgreementGate(**cfg.get("agreement_gate", {}))
        trusted = source in ("manual", "global_candidate")
        localization_mode = "verifying" if trusted else "unconfirmed"
        initial_message, initial_sent_at, initial_attempts = msg, time.monotonic(), 1
        initialized_at, healthy_count, amcl, last_amcl = time.monotonic(), 0, None, 0
        previous_pose, jump_latched = None, False
        consistency, startup_updates_remaining, amcl_odom_pose = (
            StationaryConsistencyGuard(),
            3,
            None,
        )

    endpoint.runtime(message="AMCL adapter ready; waiting for start", healthy=False)
    try:
        for event in dora:
            if event["type"] == "STOP" or event.get("id") == "finish":
                break
            if event["type"] == "ERROR":
                raise RuntimeError(str(event))
            if event["type"] != "INPUT":
                continue
            if event["id"] == "policy_command":
                command = endpoint.receive(event)
                if command:
                    if command.command in ("load_map", "set_initial_pose"):
                        try:
                            if command.command == "load_map" and "pose" not in command.inputs():
                                pass  # The supplied/cached pose is only an optional search hint.
                            else:
                                planar_pose(command.inputs().get("pose"))
                        except (ValueError, TypeError) as exc:
                            endpoint.emit(command, "rejected", str(exc))
                            continue
                    if command.command == "load_map" and state == "idle":
                        seed = planar_pose(
                            command.inputs().get(
                                "pose", load_pose_cache(cache_file, identity) or [0.0, 0.0, 0.0]
                            )
                        )
                        lifecycle_future, lifecycle_active, lifecycle_checked = None, False, 0.0
                        manager_future, manager_ready_at = None, None
                        backend.start(
                            [
                                "ros2",
                                "launch",
                                cfg["launch_file"],
                                "params_file:=" + cfg["backend_params"],
                                "mode:=localization",
                                "autostart:=false",
                            ],
                            cwd=Path(cfg["session_dir"]),
                            log_path=Path(cfg["session_dir"]) / "amcl.log",
                            env={**os.environ, "ROS_DOMAIN_ID": str(cfg["ros_domain_id"])},
                        )
                        pending, state, deadline = command, "starting", time.monotonic() + 45
                        endpoint.emit(command, "running", "loading saved map and AMCL")
                    elif command.command == "set_initial_pose" and state in (
                        "running",
                        "searching",
                        "initializing",
                    ):
                        if relocalize_request is not None:
                            endpoint.emit(
                                relocalize_request,
                                "error",
                                "manual initial pose superseded automatic search",
                            )
                            relocalize_request = None
                        search_epoch += 1
                        if pending is not None:
                            endpoint.emit(pending, "error", "superseded by a newer initial pose")
                            pending = None
                        initialize(command.inputs()["pose"])
                        pending, state, deadline = command, "initializing", time.monotonic() + 15
                        endpoint.emit(command, "running", "waiting for AMCL to accept initial pose")
                    elif command.command == "relocalize" and state == "running":
                        if relocalize_request is not None or (
                            search_future is not None and not search_future.done()
                        ):
                            endpoint.emit(
                                command, "rejected", "automatic search is already running"
                            )
                            continue
                        trusted, localization_mode = False, "searching"
                        gate.reset()
                        relocalize_request, state = command, "searching"
                        search_started, search_odom = (
                            time.monotonic(),
                            None if odom_pose is None else list(odom_pose),
                        )
                        search_epoch += 1
                        search_details = {}
                        search_future = None
                        endpoint.emit(command, "running", "正在静止全图重定位，不会驱动机器人")
                    elif command.command == "cancel_relocalization":
                        search_epoch += 1
                        if pending is not None:
                            endpoint.emit(pending, "error", "initialization canceled")
                            pending = None
                        if relocalize_request is not None:
                            endpoint.emit(
                                relocalize_request, "error", "automatic localization canceled"
                            )
                        relocalize_request, trusted = None, False
                        state, localization_mode = "running", "manual_required"
                        gate.reset()
                        endpoint.emit(
                            command, "done", "automatic localization canceled; manual pose required"
                        )
                    elif command.command == "stop":
                        backend.stop()
                        state = "idle"
                        endpoint.emit(command, "done", "owned AMCL and map server stopped")
                    else:
                        endpoint.emit(command, "rejected", "unsupported command or lifecycle state")
            for _ in range(12):
                rclpy.spin_once(ros, timeout_sec=0)
            now = time.monotonic()
            if (
                state in ("starting", "initializing", "running", "searching")
                and backend.poll() is not None
            ):
                raise RuntimeError("AMCL/map server launch exited")
            if state == "starting":
                if manager_future is None and manager.service_is_ready():
                    if manager_ready_at is None:
                        manager_ready_at = now
                    # Let both the manager and managed nodes discover response
                    # endpoints before the first state transition RPC.
                    if now - manager_ready_at > 1.5:
                        request = ManageLifecycleNodes.Request()
                        request.command = ManageLifecycleNodes.Request.STARTUP
                        manager_future = manager.call_async(request)
                if manager_future is not None and manager_future.done():
                    reply = manager_future.result()
                    if reply is None or not reply.success:
                        raise RuntimeError("AMCL lifecycle startup failed")
                if lifecycle_future is not None and lifecycle_future.done():
                    reply = lifecycle_future.result()
                    lifecycle_active = reply is not None and reply.current_state.id == 3
                    lifecycle_future = None
                elif lifecycle_future is not None and now - lifecycle_checked > 1.5:
                    # DDS can lose an early reply while a server is starting.
                    # Retry this read-only query instead of waiting forever.
                    lifecycle_future.cancel()
                    lifecycle_future = None
                if (
                    lifecycle_future is None
                    and now - lifecycle_checked > 0.25
                    and lifecycle.service_is_ready()
                ):
                    lifecycle_future = lifecycle.call_async(GetState.Request())
                    lifecycle_checked = now
                if (
                    lifecycle_active
                    and grid is not None
                    and scan is not None
                    and pub.get_subscription_count() > 0
                ):
                    initialize(seed, source="seed")
                    state = "initializing"
                elif now > deadline:
                    raise TimeoutError(
                        f"AMCL startup timeout: active={lifecycle_active}, "
                        f"map={grid is not None}, scan={scan is not None}, "
                        f"initialpose_subscribers={pub.get_subscription_count()}"
                    )
            if state == "searching":
                search_error = None
                if now - last_scan > 0.6 or now - last_odom > 0.5 or search_odom is None:
                    search_error = "重定位需要新鲜扫描与里程计，请保持机器人静止后重试"
                elif (
                    math.hypot(odom_pose[0] - search_odom[0], odom_pose[1] - search_odom[1]) > 0.08
                    or abs(
                        math.atan2(
                            math.sin(odom_pose[2] - search_odom[2]),
                            math.cos(odom_pose[2] - search_odom[2]),
                        )
                    )
                    > 0.08
                ):
                    search_error = "重定位期间机器人发生移动，请停止后重试"
                elif now - search_started > 35:
                    search_error = "自动重定位超时，请手动设置初始位置"
                elif search_future is None:
                    try:
                        # Search a snapshot independently of AMCL. Never reset
                        # its particles before a unique candidate is available:
                        # rejected/ambiguous searches must leave the filter intact.
                        ranges = np.asarray(scan.ranges)
                        ids = np.flatnonzero(
                            np.isfinite(ranges)
                            & (ranges >= 1.0)
                            & (ranges < min(scan.range_max, 15.0))
                        )
                        if scan.header.frame_id != "base_link":
                            raise ValueError("scan must be projected in base_link")
                        angles = scan.angle_min + ids * scan.angle_increment
                        points = np.column_stack(
                            [ranges[ids] * np.cos(angles), ranges[ids] * np.sin(angles)]
                        )
                        prior = load_pose_cache(cache_file, identity)
                        epoch = search_epoch

                        def search_worker(grid_copy, map_info, scan_points, cached, request_epoch):
                            result = ScanMatcher(grid_copy, *map_info).search(
                                scan_points, prior=cached
                            )
                            return request_epoch, result

                        search_future = executor.submit(
                            search_worker, grid.copy(), info, points, prior, epoch
                        )
                    except Exception as exc:
                        search_error = str(exc)
                elif search_future is not None and search_future.done():
                    try:
                        epoch, result = search_future.result()
                        if epoch == search_epoch:
                            search_details = {
                                "score": result.score,
                                "runner_up": result.runner_up,
                                "reason": result.reason,
                                "evaluated": result.evaluated,
                            }
                            if result.pose is None:
                                search_error = result.reason
                            else:
                                initialize(result.pose, source="global_candidate")
                                pending, state, deadline = None, "initializing", now + 15
                                endpoint.emit(
                                    relocalize_request, "running", result.reason, search_details
                                )
                    except Exception as exc:
                        search_error = str(exc)
                if search_error:
                    state, localization_mode, trusted = "running", "manual_required", False
                    endpoint.emit(relocalize_request, "error", search_error, search_details)
                    relocalize_request = None
            if state == "initializing":
                if amcl is not None:
                    state = "running"
                    if pending is not None:
                        endpoint.emit(
                            pending,
                            "done",
                            "AMCL returned initial pose; quality check pending",
                            {"map_file": cfg["map_file"], "initialized_at": initialized_at},
                        )
                        pending = None
                elif now > deadline:
                    state, localization_mode, trusted = "running", "manual_required", False
                    if pending is not None:
                        endpoint.emit(pending, "error", "AMCL did not accept initial pose")
                        pending = None
                    if relocalize_request is not None:
                        endpoint.emit(
                            relocalize_request, "error", "AMCL did not accept automatic candidate"
                        )
                        relocalize_request = None
                elif now - initial_sent_at > 1 and initial_attempts < 5:
                    # Only retry while no estimate exists. Never keep resetting
                    # an established filter or forcing resampling at rest.
                    initial_message.header.stamp = ros.get_clock().now().to_msg()
                    pub.publish(initial_message)
                    initial_sent_at, initial_attempts = now, initial_attempts + 1
            # AMCL already rebroadcasts map->odom for every incoming scan.
            # Do not repeatedly force resampling solely to refresh amcl_pose:
            # centimeter LIO jitter can otherwise accumulate particle diffusion.
            if (
                no_motion_future is not None
                and not no_motion_future.done()
                and now - last_update > 1.5
            ):
                no_motion_future.cancel()
                no_motion_future = None
            if (
                state == "running"
                and startup_updates_remaining > 0
                and now - last_update > 1
                and no_motion.service_is_ready()
            ):
                if no_motion_future is None or no_motion_future.done():
                    no_motion_future = no_motion.call_async(Empty.Request())
                    last_update = now
                    startup_updates_remaining -= 1
            if now - last_health >= 0.2:
                valid, reason, pose, agreement, beams = False, "等待定位", None, None, 0
                covariance = None
                pose_stamp_ns = None
                try:
                    if state not in ("running", "searching") or amcl is None:
                        raise ValueError("等待 AMCL 初始定位")
                    if now - last_scan > 0.6 or now - last_odom > 0.5:
                        raise ValueError("定位、扫描或里程计更新中断")
                    # Old amcl_pose is expected at rest. During appreciable
                    # movement, lack of a filter update is a real health failure.
                    if amcl_odom_pose is not None and now - last_amcl > 3:
                        moved = math.hypot(
                            odom_pose[0] - amcl_odom_pose[0], odom_pose[1] - amcl_odom_pose[1]
                        )
                        turned = abs(
                            math.atan2(
                                math.sin(odom_pose[2] - amcl_odom_pose[2]),
                                math.cos(odom_pose[2] - amcl_odom_pose[2]),
                            )
                        )
                        if moved > 0.15 or turned > 0.15:
                            raise ValueError("机器人已运动，但 AMCL 未更新")
                    tf = buffer.lookup_transform("map", "base_link", rclpy.time.Time())
                    stamp = tf.header.stamp.sec + tf.header.stamp.nanosec / 1e9
                    pose_stamp_ns = tf.header.stamp.sec * 10**9 + tf.header.stamp.nanosec
                    if abs(ros.get_clock().now().nanoseconds / 1e9 - stamp) > 1.5:
                        raise ValueError("定位坐标变换已过期")
                    p, q = tf.transform.translation, tf.transform.rotation
                    pose = [p.x, p.y, 2 * math.atan2(q.z, q.w)]
                    if previous_pose is not None:
                        angle = math.atan2(
                            math.sin(pose[2] - previous_pose[2]),
                            math.cos(pose[2] - previous_pose[2]),
                        )
                        if (
                            math.hypot(pose[0] - previous_pose[0], pose[1] - previous_pose[1])
                            > 0.35
                            or abs(angle) > 0.5
                        ):
                            jump_latched = True
                    previous_pose = pose
                    if jump_latched:
                        raise ValueError("定位发生跳变，请重新设置初始位置")
                    ranges = np.asarray(scan.ranges)
                    indices = np.flatnonzero(
                        np.isfinite(ranges)
                        & (
                            ranges
                            >= max(scan.range_min, cfg.get("quality_min_range_m", scan.range_min))
                        )
                        & (ranges < min(scan.range_max, 15.0))
                    )[::2]
                    if scan.header.frame_id != "base_link":
                        raise ValueError("scan must be projected in base_link")
                    angles = scan.angle_min + indices * scan.angle_increment + pose[2]
                    points = np.column_stack(
                        [
                            p.x + ranges[indices] * np.cos(angles),
                            p.y + ranges[indices] * np.sin(angles),
                        ]
                    )
                    agreement, beams = scan_agreement(grid, *info, points, tolerance=0.15)
                    cov = amcl.pose.covariance
                    covariance = [float(cov[0]), float(cov[7]), float(cov[35])]
                    if not all(
                        math.isfinite(v) and 0 <= v < limit
                        for v, limit in zip(covariance, [0.25, 0.25, 0.16])
                    ):
                        raise ValueError("定位不确定性较高，请重新设置初始位置")
                    if state == "searching":
                        raise ValueError("正在独立搜索地图位置，现有 AMCL 估计保留；暂不允许导航")
                    if not trusted:
                        raise ValueError("等待自动重定位或手动设置初始位置")
                    if not gate.observe(
                        agreement,
                        beams,
                        pose,
                        now=now,
                        sample_stamp=(scan.header.stamp.sec, scan.header.stamp.nanosec),
                    ):
                        raise ValueError("正在校核扫描贴合及定位收敛；必要时重新定位")
                    if (
                        now - initialized_at >= 4
                        and odom_pose is not None
                        and not consistency.observe(pose, odom_pose)
                    ):
                        raise ValueError(
                            "连续里程计未显示相应运动，但地图定位持续变化，请重新初始化"
                        )
                    valid, reason = True, "已有地图定位正常"
                except Exception as exc:
                    reason = str(exc)
                    if reason != "正在校核扫描贴合及定位收敛；必要时重新定位":
                        gate.reset()
                healthy_count = healthy_count + 1 if valid else 0
                healthy = (
                    healthy_count >= 5
                    and now - initialized_at >= 4
                    and startup_updates_remaining == 0
                )
                if healthy:
                    localization_mode = "tracking"
                    if now - last_cache_save > 2:
                        save_pose_cache(cache_file, identity, pose)
                        last_cache_save = now
                    if relocalize_request is not None:
                        endpoint.emit(
                            relocalize_request,
                            "done",
                            "自动重定位完成，扫描与 AMCL 收敛检查通过",
                            {"initialized_at": initialized_at, "pose": pose, **search_details},
                        )
                        relocalize_request = None
                elif (
                    relocalize_request is not None
                    and state == "running"
                    and now - initialized_at > 15
                ):
                    endpoint.emit(
                        relocalize_request,
                        "error",
                        "候选位置未通过 AMCL 校核，请手动设置初始位置",
                        search_details,
                    )
                    relocalize_request, trusted, localization_mode = None, False, "manual_required"
                dora.send_output(
                    "health",
                    pa.array(
                        [
                            json.dumps(
                                {
                                    "healthy": healthy,
                                    "reason": reason,
                                    "pose": pose,
                                    "agreement": agreement,
                                    "beams": beams,
                                    "covariance": covariance,
                                    "issued_monotonic": now,
                                    "initialized_at": initialized_at,
                                    "mode": localization_mode,
                                    "search": search_details,
                                    "acquire_threshold": gate.acquire,
                                    "tracking_threshold": gate.track,
                                }
                            )
                        ]
                    ),
                )
                if pose is not None:
                    x, y, yaw = pose
                    dora.send_output(
                        "pose",
                        Pose(
                            x=x,
                            y=y,
                            z=0.0,
                            qx=0.0,
                            qy=0.0,
                            qz=math.sin(yaw / 2),
                            qw=math.cos(yaw / 2),
                        ).to_arrow(),
                        {
                            "frame_id": "map",
                            "child_frame_id": "base_link",
                            "capture_timestamp_ns": pose_stamp_ns,
                            "clock_domain": "ros_time",
                            "localization_healthy": healthy,
                            "time_mapping_id": cfg["time_mapping_id"],
                            "ros_time_offset_ns": 0,
                            "estimate_source": "nav2_amcl",
                            "map_id": Path(cfg["map_file"]).parent.name,
                        },
                    )
                last_health = now
    except Exception as exc:
        endpoint.runtime("error", str(exc), healthy=False)
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        backend.stop()
        ros.destroy_node()
        rclpy.shutdown()
