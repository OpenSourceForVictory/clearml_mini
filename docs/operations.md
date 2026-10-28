# 运行状态

当前项目是初始工程，尚未达到 `IMPLEMENTATION_SPEC.md` 的 MVP 验收标准。API、数据库模型、保守调度器和 mock runner 有代码，但已有初始 Alembic 迁移，但缺少完整的管理员引导、真实 ClearML 适配器、对账、前端和 RoboTwin 执行环境。不要把此版本接入真实 GPU Agent。

数据库和 ClearML 之间没有共享事务。现有调度器还不能证明崩溃恢复后不重复投递，因此生产调度必须等待 stable marker 对账和故障注入测试完成。

文档管理：需求基线在 `IMPLEMENTATION_SPEC.md`，逐项能力在 `openspec/specs/`，变更计划在 `openspec/changes/`。修改行为时同步更新对应规范和任务清单。

启动 API 前设置 `DATABASE_URL`、`JWT_SECRET`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`，执行 `python -m scripts.bootstrap`。Compose 中暂不启动调度器，以免 FakeAdapter 将数据库任务标记为已投递而实际没有远程执行。

## 2026-10-28 验证记录

Docker Engine 27.2.0 可用；本机无 Compose 插件，使用临时下载的官方 Compose v5.6.1 执行 `compose -f deploy/compose.yml config --quiet`，通过。`docker build -f backend/Dockerfile -t clearml-mini-eval-api:test .` 因 Docker Hub `registry-1.docker.io` 超时失败，未生成镜像。Python 当前是 3.9.9；通过临时安装依赖执行 `PYTHONPATH=/tmp/clearml-mini-eval-deps python -m pytest -q backend/tests`，2 个测试通过。仍需在 Python 3.11+ 和真实服务上完成验收。

## ClearML SDK 版本

API 和 Runner 固定使用 `clearml==2.1.12`（2026-08-19 发布的稳定版）。此前的 1.18.0 是未验证的脚手架选择，现已更正。已验证与 PyJWT 2.9.0 可共同安装，并确认 `Task.create`、`Task.enqueue`、`Task.get_task`、`Task.get_tasks` 在 SDK 中存在；`get_console_output` 不是 `Task` 方法，日志接口实施时需按官方 SDK 核验。尚未验证与实际 ClearML Server 和 Agent 的互通。

## 国内镜像源

在当前环境中，Docker daemon 直连 `registry-1.docker.io` 返回 `connection refused`，而 DaoCloud 公共镜像代理 `m.daocloud.io/docker.io/library/python:3.11-slim` 和对应的 `postgres:16.8` 均成功拉取。Dockerfile 默认使用该代理地址，可通过 `--build-arg PYTHON_BASE_IMAGE=python:3.11-slim` 覆盖。此变更只影响项目镜像引用，不需要修改 Docker daemon 配置或重启已有容器。生产环境宜将验收过的镜像同步到自有仓库，并固定 digest。

## 国内镜像构建验证

使用 DaoCloud 代理成功拉取 Python 3.11 slim 和 PostgreSQL 16.8。Python 包使用清华 PyPI 镜像后，`docker build -f backend/Dockerfile -t clearml-mini-eval-api:dev .` 与 `docker build -f runner/Dockerfile -t clearml-mini-eval-runner:dev .` 均成功。只新建临时测试容器，未修改 Docker daemon 或现有容器。后端镜像内运行 `python -m pytest -q backend/tests`：2 passed。Runner 镜像执行 3 回合 mock 评测，输出 success_rate 0.6666666666666666，退出码 0。

## 数据库迁移验证

在项目专用的临时 PostgreSQL 16.8 容器中执行 `alembic -c backend/alembic.ini upgrade head`、`alembic check` 和集成测试，均通过。首次部署应先运行迁移，再执行 `python -m scripts.bootstrap`；bootstrap 不再调用 `create_all`。
