[English](README.md) | 简体中文

# 已有地图定位

管理地图服务和 AMCL，支持初始位姿、静止全图匹配候选、定位质量校核与位姿缓存。

## 运行要求

- Node 版本：`0.3.2`；兼容 **Forge 2**。
- Linux x86_64、Ubuntu 24.04、Python 3.12 ABI、ROS 2 Jazzy。
- Dora CLI / Python 绑定 `1.0.1`、Forge Msgs / Common `2.1.0`（精确构建依赖见 uv.lock）。
- ROS 2、算法后端、Livox 消息工作空间由部署方提供，不包含在 Node 包内。启动前加载对应 ROS setup，运行 `--check-runtime` 检查环境；该命令不连接硬件。

制品是 `directory_tar_gz`，解压后保留整个 `localization/` 目录，入口为 `localization/localization`。冻结包包含 Dora Python 绑定，须与 Dora 1.x 及 Forge 2 对等节点一起使用；旧 Forge 1 制品保持原版本，不原地替换。

## 能力边界与可选配置

- `use_sim_time: true` 显式使用仿真时间；默认仍使用宿主 ROS 时间。地图、位姿快照及地图绑定缓存由有界后台线程写入，使用同目录独占临时文件原子更新。时间戳在入队前固定，延迟落盘不刷新观测时效；磁盘错误会被报告，不阻塞定位健康发布。缓存仍只是未经信任的定位提示。

- 未指定 `launch_file` 时使用包内 ROS launch；仍支持显式自定义 launch。
- `backend_overrides` 生成参数副本；`snapshot_dir` 输出带接收时间的地图、位姿快照。
- `verify_initial_pose: true` 在定位健康校核通过后才完成初始位姿操作；未开启时保留原受理语义。健康观测包含 `recovery_required`，调用方无需解析显示文字。

这些能力通过配置启用，未配置的可选功能保持关闭。Node 仅拥有自己的后端子进程，不接管其他应用进程或写入机器人厂家工程。快照是运行观测，不是控制接口；读取方负责检查时效。

## 开发与构建

```bash
uv sync --frozen --group dev --group build
uv run localization --check-config --config config/policy.example.yaml
uv run --frozen pytest
uv run --frozen ruff check .
source /opt/ros/jazzy/setup.bash
# 按部署环境加载所需消息及算法工作空间。
BUILD_PYTHON=.venv/bin/python bash scripts/build_pyinstaller.sh
```

配置示例中的路径和话题需由使用者填入；实际执行应由 Dora dataflow 编排。构建时使用与测试相同的依赖环境。`--version`、配置检查和单元测试不替代 ROS 后端及真机验收。

接口说明见 [docs/interfaces.md](docs/interfaces.md)，配置在 `config/`，实现位于 `src/`，测试位于 `tests/`。构建工具、测试及源码不随冻结运行制品提供。

## 版本化发布

在个人 fork 修改并通过 PR 合入 main。创建与 pyproject.toml 一致的新 annotated `vX.Y.Z` tag，推送后手动运行 `Build versioned release`，指定该 tag。工作流检查源码、Forge 2/Dora 版本、依赖锁、测试和 lint，在 Ubuntu 24.04 / ROS 2 Jazzy 构建并检查真实冻结入口，再生成带 SHA256 与源码元数据的 Draft Release。发布者核查后发布，再单独登记资源平台。

制品格式为 `<entrypoint>-v<version>-ubuntu24.04-x86_64-ros2-jazzy.tar.gz`。已存在 Release 或资产不覆盖，改变字节必须使用新版本。外部 ROS 与算法后端不随包提供；构建期 Livox 仅编译经过摘要核对的官方消息定义，不运行驱动。CI 与仿真验证不代表真机验收。
