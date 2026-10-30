# Mini Eval Platform

轻量、多用户的评测工作台。控制节点运行 **API + PostgreSQL**，各评测机器运行 Python Worker，在固定 Docker 镜像中执行任务。支持单卡或多卡机器逐卡注册、按资源池匹配、公平排队、日志和指标、取消及重试。

默认可用评测类型是 **mock**，可选部署 [RoboTwin 环境自检](docs/robotwin-render.md) 验证 GPU 上的 SAPIEN 物理仿真与渲染。完整 RoboTwin 任务和模型评测尚未验收；`robotwin` 入口仍拒绝提交，不会显示模拟的真实评测结果。

## 启动控制节点

需要 Docker Engine、Docker Compose 和宿主机 Python 3。首次启动会在 `.env` 中生成管理员凭据（权限 0600）；构建镜像后运行迁移和管理员引导。

```bash
python scripts/control.py up
```

跨机器部署可指定控制节点私网地址：`python scripts/control.py up --bind-host 192.168.137.223 --port 18000`，本机浏览器访问 `http://192.168.137.223:18000`。

浏览器打开 `http://127.0.0.1:8000`，用 `.env` 中的 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 登录。API 文档在 `/docs`。宿主机不需要 Node；前端在 Docker 构建阶段编译，运行时由 API 提供。

如果 Compose 是独立可执行文件：

```bash
MINIEVAL_COMPOSE=/path/to/docker-compose python scripts/control.py up
```

脚本只管理 `mini-eval` Compose 项目。`down` 保留数据库卷，任何操作都不重启 Docker daemon。端口或私网监听地址可在 `.env` 配置，完整示例见 `deploy/example.env`。

本机部署使用 `http://127.0.0.1:18000`，因为 8000 已被其他服务占用。首次启动也可显式指定：`python scripts/control.py up --port 18000`。

无 GPU 时可在另一终端启动本地 mock Worker，随后在页面提交到 `mock` 池：

```bash
docker build -f runner/Dockerfile -t mini-eval-runner:dev .
python scripts/demo_worker.py
```

demo 凭据保存在 `.runtime/local-mock.json`（0600、Git 忽略），Ctrl+C 会停止 Worker 并尝试清理运行中的容器。

## 接入 Worker

1. 在「管理设置 → 机器管理」注册评测机器，设置机器并发、CPU、内存额度；可暂不分配资源池。
2. 确认 Worker 可访问的 API 地址并下载配置。多卡安装可加 --gpus all；凭据属于机器，可用于该机器各槽位。设备连接后在「资源池管理」选卡加入或迁移。
3. 在评测机器准备 Python 3、Docker 和 Runner 镜像。NVIDIA GPU 还需要驱动及 NVIDIA Container Toolkit。mock 模式不需要 GPU。
4. 将配置中的 `api_url` 改成 Worker 可访问的控制节点私网地址，然后安装：

```bash
docker build -f runner/Dockerfile -t mini-eval-runner:dev .
python scripts/install_worker.py --config worker-node-0.json --instance gpu0 --start
```

`--start` 启用 systemd 用户服务；没有用户级 systemd 时，省略它，按安装脚本输出的命令运行即可。每个实例有独立持久化执行记录。更多说明见 [Worker 安装](docs/worker-setup.md)。

## 验证

```bash
# Python 3.11+，安装 backend/requirements.txt 后
python -m pytest -q backend/tests
# PostgreSQL 测试需在独立测试库上先运行迁移，并设置 TEST_DATABASE_URL
alembic -c backend/alembic.ini upgrade head
alembic -c backend/alembic.ini check
# 前端构建，Node 24
cd frontend && npm ci && npm run build
```

真实 Docker smoke：在隔离测试部署上设置 `EVAL_API_URL`、`ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 后，运行 `python -m scripts.smoke_worker`。模拟机群测试运行 `python -m scripts.smoke_farm`。脚本创建测试用户、资源池和任务，不能对生产数据库运行。

## 文档与进度

- [架构和恢复协议](docs/architecture.md)
- [评测集合导入与管理](docs/suite-management.md)
- [Worker 安装](docs/worker-setup.md)
- [机器接入与资源池成员管理](docs/resource-pool-membership.md)
- [部署、备份和监控](docs/operations.md)
- [API 示例](docs/api-examples.md)
- [OpenSpec 任务](openspec/changes/lightweight-workers/tasks.md)

原始 `IMPLEMENTATION_SPEC.md` 保留为历史需求；其中 ClearML 强制依赖已由用户决定的 `lightweight-workers` 变更替代。开发使用 Git 和 OpenSpec，只有代码与对应验证完成后才勾选任务。OpenSpec CLI 使用 Node 24；进入本项目目录启动 Codex 后，可使用 `$openspec-apply-change lightweight-workers`。

GPU 调度支持不同型号共池与显式同卡共享，默认任务仍按卡独占。配置方法、显存预留和利用率的保障边界见 [GPU 调度说明](docs/gpu-scheduling.md)。

仿真客户端 SDK、可复用集合和逐回合进度：[仿真评测框架](docs/evaluation-framework.md)。

具体客户端样本：[Cosmos / RoboTwin](examples/cosmos_robotwin/README.md)，包含集合清单、SDK 适配器和镜像配方；真实服务与仿真环境在运行时接入。
