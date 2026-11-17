# 部署与运维

默认平台部署现包含 SSH 纳管执行器：scripts/control.py up 同时构建 API 与 onboarding 镜像，Compose 自动启动、健康检查和连接。复制项目到新机器后按正常平台部署即可，平台用户无需另装执行器或配置 socket。机器注册与恢复均通过界面操作。详细架构与旧宿主机部署兼容见 [SSH 纳管执行器](ssh-onboarding-executor.md)。


## 当前状态

2026-11-16 已发布 API/前端 `mini-eval-api:fleet-20261116`，数据库版本为 `r841a23`。五台物理节点 .151/.157/.177/.182/.183 已升级至 Worker 0.12.0（协议 10）、Agent 1.1.0；控制端保留本机及内网的 18000 端口入口。

五节点真实 GPU 执行、跨机客户端/服务端的宿主机和容器组合、排队及取消重试已验证。两小时多用户稳定性测试和最终清理尚待完成，详见[本轮验收报告](validation/production-fleet-acceptance/README.md)及[发布与恢复手册](fleet-release.md)。这些结果不代表单台多卡主机、NPU 或完整 RoboTwin 模型评测已验收。

2026-11-16 模板界面已单独更新至 `26be4a0`，API 进程未重启。后续容器重建使用已固定摘要的 `mini-eval-api:template-ui-26be4a0`，必须保留当前镜像与 LAN overlay；详见[界面发布记录](validation/template-authoring-guidance/README.md)。

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
| ONBOARDING_RECONNECT_TIMEOUT_SECONDS | 300 | 安装完成后的回连等待秒数，30–1800；配置在 onboarding 执行器，需重建容器加载 |
| WORKER_TTL_SECONDS | 30 | 槽位停止新准入的心跳时限 |
| LEASE_SECONDS | 30 | 已认领执行的租约 |
| LOST_WORKER_GRACE_SECONDS | 30 | 租约过期后的服务端宽限期 |
| EXECUTION_TIMEOUT_SECONDS | 3600 | 单次尝试最大运行时间 |
| STARTUP_TIMEOUT_SECONDS | 120 | 认领到首次执行心跳的时限 |
| RUNNER_IMAGE | mini-eval-runner:dev | 管理员批准的固定镜像 |

租约建议至少 15 秒，给心跳、Docker 操作及网络延迟留出余量。超时槽位会隔离，直到原 Worker 确认旧容器已停止。不能通过删除任务或清空数据库来“释放”不确定执行。

节点环境回执（宿主机环境、容器镜像/资产、存储）在版本匹配且报告时间位于过去 60 秒内时有效；GPU/NPU 遥测、主机 CPU/内存及磁盘采样在过去 45 秒内有效。两类窗口由 `backend/app/freshness.py` 统一管理，不是登录会话或 HTTP 请求超时。Worker 在线仍由 `WORKER_TTL_SECONDS` 独立判定，执行租约仍由 `LEASE_SECONDS` 控制；未来时间戳、缺失字段、检查失败及未应用的配置版本不会因窗口放宽而放行。Agent 如配置超过 45 秒的采样间隔，样本之间可能短暂不可用于调度。

## 监控

- `/healthz`：API 进程存活；Compose 使用它做健康检查。
- `/readyz`：数据库可访问。
- Worker 空闲心跳、Agent 采样和服务健康探测只覆盖数据库中的最新状态与时间戳，不逐次写节点文件，也不等待全局调度锁。同一节点或服务的并发上报按记录串行；携带执行租约的 Worker 心跳仍需协调调度，锁忙会快速返回 503（`Retry-After: 1`），由 Worker 重试。若出现大量 503、数据库连接池超时或五秒 HTTP 超时，检查 PostgreSQL 活动事务和等待事件、API 日志及调度慢操作；不要靠无限增大连接池掩盖长事务，也不要删除执行记录。
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

## R8000 部署与验证（2026-10-29）

- 服务地址：`http://192.168.137.223:18000`，R8000 上的项目目录为 `/home/huawei/clearml_mini`。API 提供前端，PostgreSQL 不映射宿主机端口；两者设置 `restart: unless-stopped`。
- GPU：Quadro RTX 8000，48 GiB，驱动 `580.178.04`。补齐同版本 `libnvidia-gl-580-server` 图形库，安装 Docker/Compose 和 NVIDIA Container Toolkit；使用 NVIDIA runtime 模式，无需重启 Docker daemon。
- 常驻 Worker 使用系统服务 `mini-eval-worker-r8000.service`，以 `huawei` 用户和 Docker 附加组运行，已启用开机启动。配置在 `/home/huawei/clearml_mini/worker-r8000.json`（0600），执行记录使用同路径的 `.state` 文件。
- 控制节点启用 `ROBOTWIN_RENDER_ENABLED=1`，固定 Runner 为 `mini-eval-runner:robotwin-render`，资源池 `r8000` 每任务 2 CPU/4 GiB，机器最大并发 1。
- 隔离 PostgreSQL 测试库上迁移、schema check 和最终 22 项后端测试通过。真实 GPU mock smoke 验证容器只看到指定 UUID，并通过 Worker SIGKILL 恢复、取消、注入失败重试、失联超时、隔离和清理后重试。
- 3 用户 × 5 任务的模拟 1+2 槽位机群通过，全局峰值并发 2、每任务仅 1 次尝试；此项仍不代表真实多卡验收。
- 本机 Playwright 验证登录、提交、取消/重试、资源与管理页面；另从本机浏览器提交真实 `robotwin-render` 3 回合任务，成功率 100%，图像标准差约 0.152、方块落地高度约 0.10 米，Runner 耗时约 0.87 秒，只有一次执行尝试且清理已确认。
- 示例任务 ID：`b99c8a35-42ac-46bc-9e5a-fa33bf04d48e`。日志中的 SAPIEN Vulkan ICD 自动补全与旧版 Engine 弃用提示未影响检查；实际图像和物理断言均通过。
- 完整 RoboTwin 任务/模型评测与真实多卡验收仍保留未完成状态。环境自检说明见 [robotwin-render.md](robotwin-render.md)。

R8000 Worker 运维命令：

```bash
sudo systemctl status mini-eval-worker-r8000
sudo journalctl -u mini-eval-worker-r8000 -n 100
sudo systemctl restart mini-eval-worker-r8000
```

## 本机 RTX 4090 纳管（2026-10-29）

- 本机 `Huawei` 的 RTX 4090（约 24 GiB）注册为 `local-4090` 节点和同名资源池，连接 R8000 的 API。
- 每机并发 1，每任务 2 CPU/4 GiB RAM。现有池按 GPU 型号匹配，RTX 4090 与 RTX 8000 分池；不会自动按实时空闲显存或 GPU 利用率调度，也不计入平台外进程的 GPU 占用。
- Worker 以 `dgh` 用户及 Docker 附加组运行，系统服务 `mini-eval-worker-local4090` 已启用开机启动。配置位于本机项目 `.runtime/worker-local4090.json`（0600）。
- 复用 R8000 已验证的 `mini-eval-runner:robotwin-render` 镜像；NVIDIA runtime 容器内核验只见本机指定 GPU。
- 平台任务 `fee95e01-9429-4c05-872b-f142d90f5061` 通过 3 回合真实渲染自检，Runner 耗时约 0.90 秒、仅一次执行尝试、清理已确认。两台机器均为单卡，仍不替代多卡机器验收。

```bash
sudo systemctl status mini-eval-worker-local4090
sudo journalctl -u mini-eval-worker-local4090 -n 100
```

## 2026-10-29 异构池与共享调度升级

OpenSpec 变更：`shared-gpu-scheduling`。R8000 服务端已迁移到 `b42e901`，两台 Worker 都使用协议 v2。

- 统一资源池 `gpu-pool`：`8af54597-aa7a-4316-a614-326c13ebd2d1`，含 Quadro RTX 8000 / 48 GiB 和 NVIDIA GeForce RTX 4090 / 约 24 GiB。
- 允许显式共享，每卡最多 2 个任务，安全余量 2 GiB，共享准入要求 GPU 利用率低于 90%。节点并发上限各 2；全局/每用户并发仍为 2。默认任务仍独占 GPU。
- 原 `r8000`、`local-4090` 空池已禁用，历史任务保留。未来提交选择 `gpu-pool`。
- 4090 当前平台外 `wildrig-multi` 进程使 GPU 利用率达到 100%；未停止该进程，新共享任务由准入条件排除这张卡。可在计算资源页观察最新采样。

验收：Python 3.11 容器内 29 项测试通过；隔离 PostgreSQL 16 的 3 项集成测试通过，包括 8 个并发调度器下共享显存不超额、事务领导锁和迁移约束，Alembic check 无差异。浏览器常规工作流、共享配置/任务需求 2 项通过，生产渲染自检另 1 项通过。

真实 GPU 并发验收在隔离 API/数据库及 RTX 8000 完成：两个真实 Docker 容器同时只看到指定 GPU，第三个排队；SIGKILL Worker 后重启保持容器 ID 不变；取消一个任务不影响另一个，第三个随后执行；每任务仅一次尝试且清理确认。模拟任务验证并发编排和 GPU 可见性，不证明 CUDA 显存硬隔离；另有 SAPIEN 真渲染任务成功。两个节点各一张卡，不替代多卡主机硬件验收。

测试任务 ID（隔离数据库）：`4be231cf-8606-4d4e-9690-e4c55713e5ff`、`5d19a0e6-ad55-43b4-89ce-7508116257f8`、`c88fee55-0557-4d12-a010-11d98319e2c9`；渲染 `65a603c3-c752-4881-a2c5-193a8dc289b6`。可用 `scripts/smoke_shared_gpu.py` 在隔离部署重复验收。

回滚材料在 R8000：`/home/huawei/mini-eval-before-shared.dump`（及校验文件）、`/home/huawei/clearml-mini-before-shared.tar.gz`、镜像 `mini-eval-api:before-shared`。回滚采用升级前数据库快照恢复，不只执行 schema downgrade；先排空并停止 API/两台 Worker，再恢复数据库与代码，将两个 Worker 配置的 `pool_id` 改回恢复后数据库中各自 `r8000`/`local-4090` 池 ID，恢复节点服务。数据库快照会丢失升级后任务，正式回滚前另备份当前数据库。

## Worker 存储切换与回退

先升级数据库/API、前端，再升级空闲 Worker。数据库迁移 `c396d08` 新增机器配置及逐卡报告，旧记录默认 revision=0/config=null。首次 v4 启动保留本机目录，并完成挂载验证；需要本地已安装且包含 Python 的批准 runner 镜像。

保存新版本与 claim 共用 PostgreSQL 调度锁。未领取的 RESERVED 返回 PENDING 并释放 GPU/CPU/内存预留；STARTING/RUNNING/CANCEL_REQUESTED 及待清理执行保持原位。每张卡空闲后独立切换。页面的最后报告过期、校验失败或旧 Worker「需升级」都意味着不能领取新任务。

回退先在界面恢复本机配置，等待所有槽位确认、排空任务并确认容器清理后，再降级 Worker/API 和数据库。数据库降级前备份；目录、执行 journal 与 `.storage.json` 不得随数据库回滚删除。存储设置不会改变 Docker 自身镜像/层的 data-root，也不会自动迁移旧执行文件。
