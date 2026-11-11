# 已有地图定位

管理地图和 AMCL，支持初始位姿、静止全图匹配候选、定位质量检查和位姿缓存。

## 开发与运行

需要 Python 3.12、Forge Msgs 2.x、Common 2.1、Dora 1.1，实际运行使用 ROS2 Jazzy。

```bash
uv sync --group dev
uv run localization --check-config --config config/policy.example.yaml
uv run pytest
uv run ruff check .
uv pip install --python .venv/bin/python -r requirements-build.txt
source /opt/ros/jazzy/setup.bash
# 若使用 Livox 消息，还需 source 对应 workspace/install/setup.bash
BUILD_PYTHON=.venv/bin/python bash scripts/build_pyinstaller.sh
```

示例配置需要填入实际 topic、后端程序和资源路径。节点通过 Dora 运行，不能把命令行启动视为完整 dataflow。`examples/dataflow.yaml` 展示连接端口；调用方提供上游节点和控制命令。

## 接口

详见 [接口说明](docs/interfaces.md)。配置放在 `config/`，节点实现位于 `src/localization/`，测试位于 `tests/`。节点负责自己的后端子进程；不接管已有进程，不写入机器人官方工程。

## 二进制运行环境

制品为 `directory_tar_gz`，解压后保留整个目录，入口与节点同名。
目标平台：Linux x86_64、Ubuntu 24.04、Python 3.12 ABI、ROS2 Jazzy。
ROS2、算法后端和 Livox 消息工作空间由部署方提供，不包含在 Node 压缩包中。
启动前 source ROS2 和所需消息工作空间，先运行 `--check-runtime` 检查动态消息类型支持；该检查不连接硬件。
构建使用 `BUILD_PYTHON` 指定已安装项目和构建依赖的 Python。源码测试、构建、运行检查应在同一锁定环境执行。
