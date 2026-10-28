# 部署与运维

## 当前状态

控制节点、轻量 Worker、mock Docker 评测、调度、取消/重试、日志/指标和 Web UI 已实现。实际验证记录见本文件末尾。真实单卡/多卡 GPU 和 RoboTwin 尚未验收；不将模拟槽位测试计作真实硬件验收。

## 启停

```bash
python scripts/control.py up
python scripts/control.py status
python scripts/control.py down
```

Compose 项目名固定为 `mini-eval`。常驻服务只有 `api`、`db`；`migrate`、`bootstrap` 是执行后退出的初始化任务。API 同时提供前端和调度循环。脚本仅管理本项目，不重启 Docker daemon，不停止其他容器。停止控制节点保留 PostgreSQL 卷。

初始账号和随机密码保存在项目 `.env`（0600、Git 忽略）。修改 `.env` 的初始密码不会重置已有管理员密码。当前账户若无 Compose 插件，可设置 `MINIEVAL_COMPOSE=/tmp/docker-compose-clearml` 使用已有独立可执行文件。

默认只监听 `127.0.0.1:8000`。跨机器使用时，将 `API_BIND_HOST` 设置成控制节点私网地址或通过 TLS 反向代理提供服务，Worker `api_url` 与该入口一致。数据库不映射宿主机端口。端口冲突时修改 `API_BIND_PORT`，再运行 `up`。

镜像默认使用 DaoCloud Python/Node/PostgreSQL 地址，Python 包使用清华镜像。可通过 Docker build args `PYTHON_BASE_IMAGE`、`NODE_BASE_IMAGE`、`PIP_INDEX_URL` 覆盖。已验证 ARM64 控制节点构建。Runner 镜像在 GPU 节点本地构建或同步到内部仓库；部署时建议固定经验证的镜像版本或 digest。

## 参数

| 变量 | 默认值 | 含义 |
|---|---:|---|
| MAX_ACTIVE_GLOBAL | 2 | 全局活动尝试上限，包含预留和取消中 |
| MAX_ACTIVE_PER_USER | 2 | 每用户活动尝试上限 |
| MAX_PENDING_PER_USER | 20 | 每用户排队任务上限 |
| WORKER_TTL_SECONDS | 30 | 槽位停止新准入的心跳时限 |
| LEASE_SECONDS | 30 | 已认领执行的租约 |
| LOST_WORKER_GRACE_SECONDS | 30 | 租约过期后的服务端宽限期 |
| EXECUTION_TIMEOUT_SECONDS | 3600 | 单次尝试最大运行时间 |
| STARTUP_TIMEOUT_SECONDS | 120 | 认领到首次执行心跳的时限 |
| RUNNER_IMAGE | mini-eval-runner:dev | 管理员批准的固定镜像 |

租约建议至少 15 秒，给心跳、Docker 操作及网络延迟留出余量。超时槽位会隔离，直到原 Worker 确认旧容器已停止。不能通过删除任务或清空数据库来“释放”不确定执行。

## 监控

- `/healthz`：API 进程存活；Compose 使用它做健康检查。
- `/readyz`：数据库可访问。
- 管理员调用 `/api/v1/admin/health` 查看调度器最近成功 tick 的时间及隔离槽位数；超过 10 秒未更新应检查 API 日志。
- Web「计算资源」查看心跳，「管理设置」查看机器额度；普通用户只能看到槽位数量汇总。
- `docker compose -p mini-eval -f deploy/compose.yml logs --tail=200 api` 查看控制节点错误；执行时使用与启动一致的 `--env-file`。

日志最多保存每次尝试 256 KiB，指标独立保存，因此日志截断不会丢失成功率。大资产和 checkpoint 尚不通过平台上传，RoboTwin 阶段再确定共享存储或 S3 契约。

## 备份与恢复

```bash
python scripts/control.py backup --output /safe/path/mini-eval.dump
sha256sum -c /safe/path/mini-eval.dump.sha256
```

备份使用 `pg_dump -Fc`，可在正常运行时生成一致性快照。备份文件含账户摘要、Worker 凭据摘要、任务和日志，权限为 0600。另行安全备份 `.env` 和 GPU 节点的 Worker 配置/执行记录。

恢复时先停止新准入和 Worker，在独立的新 PostgreSQL 实例中创建空 `eval` 数据库，然后执行 `pg_restore -U eval -d eval --no-owner mini-eval.dump`。先验收数据和迁移版本，再切换 API。不要对已有数据库直接覆盖；不要清空数据库来回滚软件。

Alembic 提供两次新增迁移及 downgrade。回滚前先停止 API/Worker 并备份；退回旧 schema 会删除新 Worker 表和执行字段，不能保留新执行链路的运行中状态。

## 验证记录（2026-10-28）

- ARM64 API 与 Runner 镜像构建通过；API 镜像包含 React 编译产物。
- PostgreSQL 16.8 上 `alembic upgrade head`、`alembic check` 通过；后端 20 项测试通过，包含数据库约束和 8 个并发调度调用。
- `scripts/smoke_worker.py`：真实 Docker 执行、日志和指标、SIGKILL 后恢复原容器、运行中取消、注入失败和新尝试重试、Worker 失联超时、隔离及清理后重试均通过。
- `scripts/smoke_farm.py`：3 用户 × 5 任务，3 个真实 Worker 进程模拟 1+2 槽位节点，全局峰值并发 2，全部成功且每任务仅 1 次尝试。
- 模拟机群在同一宿主机运行，不是两台真实 GPU 机器。RoboTwin 上游版本、checkpoint 和资产尚未确定，未执行真实 RoboTwin 验收。

- Playwright 浏览器验收通过：登录、提交、取消、重试、成功指标/日志、资源页、管理员机器注册与配置下载，以及移动宽度导航。浏览器依赖安装在专用测试镜像中。
- `control.py up --port 18000` 已启动本机实例；备份校验和恢复到独立数据库通过；迁移 upgrade → downgrade base → upgrade → check 通过。
- Worker 安装和 systemd 单元语法验证通过；本机没有可用的用户 systemd 会话，因此实际运行采用安装后的 Python 客户端，已完成正式实例的 demo 任务。
