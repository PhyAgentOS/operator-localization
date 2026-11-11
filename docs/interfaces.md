# 接口

输入：tick、finish、policy_command（PolicyCommand，policy_id=localization）。命令：load_map / set_initial_pose / relocalize / cancel_relocalization / stop。输出：status（PolicyCommandStatus，保留 request_id）。输出 pose（Pose）、health（单元素 Arrow string 数组中的 JSON）。健康状态含单调时间戳，仅用于同机控制。

ROS2 的 LaserScan、Odometry、OccupancyGrid、Path、TF 在后端交换，不新增 Forge 消息类型。具体配置键见示例 YAML；topic、ROS domain、程序路径由调用方指定。

## 最小调用

```python
from forge_msgs import PolicyCommand

request = PolicyCommand.from_inputs(
    policy_id="localization", command="load_map", request_id="example-1", inputs={"pose": [0, 0, 0]}
)
node.send_output("policy_command", request.to_arrow())
```

每次新任务使用唯一 request_id；重复相同请求返回已有状态，重复 ID 配不同参数被拒绝。status 常见值为 accepted/running/done/rejected/error；done 是此命令完成，不表示后端进程退出。

| command | inputs 示例 | 作用 |
|---|---|---|
| `load_map` | `{"pose": [0, 0, 0]}` | 加载配置中的地图；pose 为可选初始提示 |
| `set_initial_pose` | `{"pose": [1, 2, 0.5]}` | 指定地图坐标中的 x(m)、y(m)、yaw(rad) |
| `relocalize` | `{}` | 静止扫描匹配搜索；歧义时请求人工定位 |
| `cancel_relocalization` | `{}` | 取消搜索 |
| `stop` | `{}` | 停止 AMCL 和地图服务 |

`examples/dataflow.yaml` 展示完整输入输出连接。dynamic 节点是调用方占位，不会凭空提供数据；实际应用需用自己的节点替换。`tick` 为调度输入，`finish` 任意负载触发退出。所有进程在同一机器人运行，统一 ROS_DOMAIN_ID。

## 健康输出

`health` 为单元素 Arrow string 数组中的 JSON，包含 `healthy`、`issued_monotonic`、reason、pose、agreement、covariance、mode 等诊断。单调时间仅适用于同机进程，下游 navigation 使用它检查定位健康状态的时效。
