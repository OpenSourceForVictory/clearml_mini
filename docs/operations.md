# 部署与运维

## 未领取任务的预约期限

`RESERVATION_SECONDS` 默认 30 秒，表示资源已预约、Worker 尚未领取时的有效窗口，不是程序运行时限。普通新预约在本轮调度计算及主要写入完成后、事务提交前重新设置期限，避免较慢的调度计算提前耗尽窗口。`reserved_at` 仍记录原预约决策时间，因此两时间字段之差不再等同于配置值。

托管客户端等待服务就绪时，保留服务启动时限加 `STARTUP_TIMEOUT_SECONDS` 的等待预算；服务就绪后，本轮提交前为其开启普通领取窗口。已有预约不会每轮自动续期，未领取过期仍按原规则回到排队；已启动任务的心跳租约、执行时限及清理规则不变。

最终期限写入、提交和 Worker 轮询仍会消耗窗口；数据库提交长时间阻塞不能仅靠这个修复消除。领取时仍检查期限、节点和服务就绪，遥测不会因窗口刷新变新。修复触发条件、失败证据和专项验证见 [预约窗口验证](validation/reservation-window-20261124/README.md)。此修复无需迁移数据库或升级 Worker，发布时更新运行调度器的 API；记录中的代码验证不代表现场已部署。

## 控制通道现状

连接池背压及双进程隔离于 2026-11-23 在本机启用，2026-11-25 已升级为[四角色 API 隔离](priority-api-isolation.md)，保留原访问地址；当前仍未测得可承诺的 Worker 容量上限。Worker 的 HTTP 客户端保留 `Retry-After`（秒数或 HTTP 日期），单次调用不自动重放；执行调用层将 429/502/503/504 和网络超时视为暂时故障，在 Retry-After 指定的最早重试时间后增加最长 2 秒的随机退避，等待仍受剩余租约约束。回合、日志、产物及完成上报暂时失败后返回心跳循环，确认接收前不推进可靠数据游标。400/401/403/404/409/413/422 不属于过载重试；既有清理和日志配额规则仍有效。本次同时实现默认关闭的数据库连接隔离与锁等待限制，详细验收进度见 OpenSpec `worker-control-plane-backpressure`。

## 单/双进程连接预算（兼容部署）

基础 Compose 保持原启动方式，背压开关 `DB_BACKPRESSURE_ENABLED` 默认 `0`；本机现使用四角色覆盖配置；本节五类池参数仅适用于单/双进程，四角色改用 `PRIORITY_API_POOL_SIZES`，见上面的部署指南。其他部署先验证并升级目标 Worker 至 0.21.16，再重建 API。不要仅改环境文件后执行 restart，已有容器不会重新读取 Compose 环境。迁移和备份不使用在线短超时。连接预算本身不要求新表，但当前工作树同时包含 Agent 观察状态分表迁移 `ad074e5`（前置 `ac963d4`），需先停止旧 API 写入并迁移，不能混跑新旧 API；分表已在本机迁移至 `ad074e5`；100 Worker 混合负载验证及容量边界见四角色验收记录。

| 设置 | 默认 | 含义 |
| --- | --- | --- |
| `DB_POOL_SIZE` | 10 | 每个 API 进程日志、产物、Web 和领取的普通连接上限 |
| `DB_CONTROL_POOL_SIZE` | 5 | 每进程 Worker 心跳、服务健康/探测会话、完成、清理、停止许可和取消/控制操作的保留池 |
| `DB_OBSERVATION_POOL_SIZE` | 2 | Agent 最新监控独立池，不借用执行控制池 |
| `DB_RESULT_POOL_SIZE` | 2 | 回合和任务级可靠结果独立池 |
| `DB_SCHEDULER_POOL_SIZE` | 1 | 后台调度独立池，不被 HTTP 请求借用 |
| `API_DB_PROCESS_COUNT` | 1 | 实际共享数据库的 API 进程总数，部署多副本时必须同步填写 |
| `DB_CONNECTION_BUDGET` | 80 | 上述所有 API 池合计允许占用的连接预算 |
| `DB_POOL_TIMEOUT_SECONDS` | 0.25 | 借连接最长等待 |
| `DB_LOCK_TIMEOUT_SECONDS` | 0.5 | 在线语句锁等待上限 |
| `DB_REQUEST_TIMEOUT_SECONDS` | 4 | 从入口开始计算的在线 SQL 累计预算 |

五类池均不允许 overflow。单进程部署仍创建五类池，启动校验 `(普通 + 控制 + 监控 + 结果 + 调度池) × API 进程总数 <= 总预算`。可选双进程部署按角色只创建实际使用的池：普通进程创建普通、观察、结果、调度池；控制进程只创建控制池。两进程读取同一组类别额度并校验其总和，不将五类池重复乘二。总预算应低于 PostgreSQL 可用连接数，并扣除其他应用、备份及管理员连接；该校验依赖部署者保持两进程类别额度一致，不会自动发现额外副本，也不自动调整 PostgreSQL `max_connections`。

所有 HTTP 入口（包括静态文件和 SPA）按服务端路由分类，在鉴权和借连接前检查并发额度；控制类有短时有界 FIFO 等待，其余类别超额快速拒绝。同步线程额度至少容纳各类已准入请求及清理余量，不能用普通请求占满准入额度之外的线程；这不提供 CPU 或磁盘隔离。鉴权和处理共用同类 Session。空闲卡位的领取先只读检查，不争用全局调度锁；存在候选执行或待清理记录时才尝试非阻塞锁，并在锁内复核。候选时锁忙按本轮无任务处理，Worker 下轮继续领取；数据库连接或网络故障仍按可重试错误处理。Worker 0.21.17 的领取退避单次总等待最长 3 秒，且不设重试次数上限；HTTP 请求本身另有 5 秒超时。每条 SQL 根据剩余请求预算，通过一次配置往返同时设置事务内 statement_timeout 和 lock_timeout；请求预算耗尽后不再执行新 SQL。它不负责取消任意 Python 运算或文件流，连接建立、线程调度和网络开销仍须通过容量测试确认。连接池耗尽、已识别的锁/语句超时、死锁及序列化冲突返回 503 和 Retry-After；数据约束、非法 SQL 等错误不伪装为过载。

普通请求拥塞不会借用控制池，但数据库整体资源不足、同一行锁竞争仍能影响续租。日志中的 `category` 和 `reason` 可区分入口并发、连接池、行锁及语句预算；不要把连接数换算成可支持 Worker 数。真实混合负载上限尚未验证。

### 可选双进程 API 隔离（本机此前使用，可回退）

新的四角色隔离实现将执行控制、用户交互、Worker 数据与批量传输分别运行，默认总连接上限仍为 80；其独立覆盖文件、健康检查、容量参数和回退步骤见[四角色 API 隔离](priority-api-isolation.md)。它是独立的显式部署选项，不叠加在双进程覆盖文件之后；当前发布及验收进度以该文档链接的验证记录为准。

2026-11-23 本机先备份并在隔离 PostgreSQL 完成恢复及 `ab852c3 → ad074e5` 迁移演练，随后将生产数据库迁移至 `ad074e5`，逐台受控升级六台机器的 13 个 Worker 卡位至 0.21.16，再启用双 API。原回环和私网入口均返回健康 200；真实本机 GPU 小任务经网关成功、退出码 0、清理已确认。该任务使用 SDK 演示业务，不是模型评测或 100 Worker 生产容量验收。发布记录见[双进程验证](validation/split-control-api-process-20261123/README.md)。

逐条操作步骤见 [已有平台双 API 启动与更新](split-api-startup.md)，包含配置核对、备份、迁移及验证。

`deploy/split-api-compose.yml` 在基础 Compose 之上增加独立控制 API 和 Nginx 网关。两个 API 容器各自在内部监听 8000，只有网关绑定原 `API_BIND_HOST:API_BIND_PORT`；浏览器和 Worker 的平台地址不变。控制进程只接收执行控制 POST（续租、服务状态/探测、完成/清理、停止许可、取消/服务控制），普通进程处理 Agent 监控、领取、结果、日志、产物和 Web。两个进程的 FastAPI 同步线程池独立，普通进程运行调度器，控制进程设置 `SCHEDULER_ENABLED=0`。误路由在鉴权和数据库访问前返回 404；网关不会在控制进程故障时回退到普通进程。

启用时必须使用 Compose v2.24 或更新版本（覆盖文件使用 `!reset` 清除原 API 端口），并确认目标 Worker 能正确处理暂时性 503；两进程启用 `DB_BACKPRESSURE_ENABLED=1`、`DB_ADMISSION_ENABLED=0` 和 `API_DB_PROCESS_COUNT=2`。`DB_ADMISSION_ENABLED=0` 取消新增的分类入口并发闸门；数据库连接池仍有界，连接等待超时仍会返回可重试 503。双进程使用同一组类别额度，默认普通/控制/观察/结果/调度为 23/40/9/7/1；普通 API 实际创建 23+9+7+1=40 个连接上限，控制 API 创建 40 个，合计最多 80 个，分别对齐每进程默认 40 个同步线程额度。普通、观察、结果和控制主要预算可分别用 `DB_SPLIT_ORDINARY_POOL_SIZE`、`DB_SPLIT_OBSERVATION_POOL_SIZE`、`DB_SPLIT_RESULT_POOL_SIZE`、`DB_SPLIT_CONTROL_POOL_SIZE` 覆盖；改动后用 Compose 展开结果核对两进程参数一致及总预算。当前 PostgreSQL `max_connections=100`、保留管理员连接 3 个；80 只是 API 理论上限，还必须计入其他连接，启用前检查实测余量，不要仅因配置通过就上线。池大小是同时持有的数据库连接上限，并非 Worker 数或同类 HTTP 入口并发上限；同步线程额度另由 AnyIO 管理，仍共享主机 CPU。不要同时使用原 `deploy/lan-compose.yml` 给 `api` 重新绑定端口；需要第二个私网入口时，在双进程覆盖文件后再加 `deploy/split-api-lan-compose.yml`，由网关绑定该地址。启用前检查现有执行、完成数据库备份并使用原环境文件验证配置：

```bash
docker compose --env-file .env -p mini-eval -f deploy/compose.yml -f deploy/split-api-compose.yml config --quiet
docker compose --env-file .env -p mini-eval -f deploy/compose.yml -f deploy/split-api-compose.yml up -d --wait
docker compose --env-file .env -p mini-eval -f deploy/compose.yml -f deploy/split-api-compose.yml ps
```

若本机原来使用 `scripts/control.py` 或其他覆盖配置启动，应在同一环境文件、项目名和覆盖顺序下切换；`scripts/control.py` 当前只管理基础 Compose，不能用于双进程启动/停止。回退到基础 Compose 时，先用当前双进程 Compose 停止 `api-gateway` 和 `control-api` 以释放原端口，再移除双进程覆盖文件、保留当前已迁移版本的 API 镜像并按现有发布流程重建单 API；核对普通进程是唯一调度器。不要直接启动迁移前的旧 API 镜像或执行数据库降级。网关 `/healthz` 同时检查两组 API；控制进程故障时该健康检查失败，但普通接口仍可用。此隔离不解决 PostgreSQL 全局负载、同一行锁、主机 CPU/磁盘竞争；当前不作为 Worker 容量承诺。

隔离混合负载可用 `backend/tests/control_plane_load.py --split-api --role-aware-pools --no-admission --ordinary-pool 23 --control-pool 40 --observation-pool 9 --result-pool 7 --client-per-worker --agent-telemetry --independent-logs` 复现；它会新建并删除独立测试数据库，在随机本机端口启动两个 API 和临时 Nginx，**不得指向生产数据库**。此前 26 连接配置下 100 Worker 60 秒短测通过，最长心跳间隔 3.59 秒，全部 3000 条日志最终确认，仍有 247 次连接池 503 和 682 次领取调度锁 503。40/40 配置的专项结果见[双进程混合负载专项](validation/split-control-api-process-20261123/load/README.md)。观察池大小是同进程观察类数据库连接上限，并非可接入节点数；双进程下 Agent 只进入普通 API。不要按短测宣称生产 100/200 Worker 容量。

排查期间可临时设置 `DB_BUDGET_DIAGNOSTICS=1` 并重建 API，输出每请求首次借连接的 `wait_ms`、池内 `checked_out` 和请求 `elapsed_ms`，不输出 SQL 参数或业务内容。该诊断会增加日志量，平时默认关闭；commit 后再次借连接仍受池预算限制，但不计入首次借连接指标。

现有平台升级、数据库备份、Compose 自动迁移及失败处理见 [数据库迁移与 Compose 升级指南](database-migration.md)。可在其他部署机器按该文档执行，须沿用该机器的环境文件、项目名与覆盖配置。

2026-11-20 批次排队优先级已部署到本机 `mini-eval` Compose 平台，数据库迁移版本为 `aa741b2`。无需升级 Worker。此部署仅在无非终态任务时重建 API，并先生成数据库备份；验证范围及未覆盖的真实队列操作见 [批次优先级验证记录](../openspec/changes/archive/2026-11-20-batch-priority/validation.md)（归档后路径）。

2026-11-18 宿主机登录环境与逐回合启动发布：控制端镜像 `mini-eval-api:host-shell-episode-20261118`，数据库版本 `w396f78` 无迁移。29 号 NPU 节点与本机 GPU 节点升级 Worker 0.18.0 / 协议 15；其他节点保持旧版本，登录环境或逐回合模板会按协议门槛排除旧节点。发布、备份、真实任务及清理边界见 [验证记录](validation/host-shell-episode-launch/README.md)。

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

Compose 项目名固定为 `mini-eval`。常驻服务为 `api`、`db` 和 SSH 纳管执行器 `onboarding`；`migrate`、`bootstrap` 是执行后退出的初始化任务。API 同时提供前端和调度循环。脚本仅管理本项目，不重启 Docker daemon，不停止其他容器。停止控制节点保留 PostgreSQL 卷。

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
| EXECUTION_TIMEOUT_SECONDS | 3600 | 新客户端有限时长的上限；无客户端快照任务的执行默认值 |
| STARTUP_TIMEOUT_SECONDS | 120 | 认领到首次执行心跳的时限 |
| RUNNER_IMAGE | mini-eval-runner:dev | 管理员批准的固定镜像 |

### 放宽全平台和每用户并发额度

在实际部署使用的环境文件（标准部署为项目根目录 `.env`）设置：

```dotenv
MAX_ACTIVE_GLOBAL=1000000
MAX_ACTIVE_PER_USER=1000000
```

这表示将两个总量保护阈值提高到一百万，不是关闭资源调度。客户端和托管服务共用额度，资源池共享规则、节点并发、设备/CPU/内存及批次客户端并发仍生效。未显式设置时，代码与 Compose 的默认值仍是 2。

修改后使用当前部署的同一组 Compose 文件和镜像 overlay，仅重新创建 API 服务（`up -d --no-deps --no-build --wait api`）；普通 `restart` 不加载新环境变量。操作前检查活动执行及清理记录，操作后核对 API 容器中的这两个变量和健康状态。不要输出完整环境或凭据，也不要因为修改额度而意外构建/切换镜像或升级 Worker。

租约建议至少 15 秒，给心跳、Docker 操作及网络延迟留出余量。超时槽位会隔离，直到原 Worker 确认旧容器已停止。不能通过删除任务或清空数据库来“释放”不确定执行。

节点环境回执（宿主机环境、容器镜像/资产、存储）在版本匹配且报告时间位于过去 60 秒内时有效；GPU/NPU 遥测、主机 CPU/内存及磁盘采样在过去 45 秒内有效。两类窗口由 `backend/app/freshness.py` 统一管理，不是登录会话或 HTTP 请求超时。Worker 在线仍由 `WORKER_TTL_SECONDS` 独立判定，执行租约仍由 `LEASE_SECONDS` 控制；未来时间戳、缺失字段、检查失败及未应用的配置版本不会因窗口放宽而放行。Agent 如配置超过 45 秒的采样间隔，样本之间可能短暂不可用于调度。

## 监控

- `/healthz`：API 进程存活；Compose 使用它做健康检查。
- `/readyz`：数据库可访问。
- Worker 空闲心跳、Agent 采样和服务健康探测只覆盖数据库中的最新状态与时间戳，不逐次写节点文件，也不等待全局调度锁。同一节点或服务的并发上报按记录串行；携带执行租约的 Worker 心跳仍需协调调度，锁忙会快速返回 503（`Retry-After: 1`），由 Worker 重试。若出现大量 503、数据库连接池超时或五秒 HTTP 超时，检查 PostgreSQL 活动事务和等待事件、API 日志及调度慢操作；不要靠无限增大连接池掩盖长事务，也不要删除执行记录。
- 管理员调用 `/api/v1/admin/health` 查看调度器最近成功 tick 的时间及隔离槽位数；超过 10 秒未更新应检查 API 日志。
- 修改 Agent 配置、单卡目录、纳管状态或升级回执时，API 先读取同节点行锁保护的最新 connection，再更新对应字段；与心跳并发时不会用旧 JSON 覆盖新样本。普通 Agent 心跳仍不获取全局调度锁。重复序号保持最新数据，重新纳管主动撤销旧会话的行为不变。此修复只涉及 API，无数据库迁移或 Worker 协议升级。

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

调度时间检查：自动 tick 在读取候选节点和设备后刷新准入时刻，避免将 tick 开始后合法提交的新报告误判成未来数据；真正未来的时间戳和超出有效期的样本仍不准入，不通过延长 TTL 解决。首次执行握手 503 重试需要 Worker 0.18.1，单独更新 API 不会更新已纳管 Worker。

### 客户端总执行时限

业务评测向导的客户端步骤可设置“每项总超时秒”，全部回合共用，不包含排队等待。界面通过已认证的 `GET /api/v1/execution-limits` 显示平台上限，初始值为 300 秒与上限中的较小值。读取失败需刷新执行时限后继续，已编辑值会保留；超出上限明确拒绝，不自动截短。勾选“不限制每项总执行时长”会提交 `client.timeout_seconds=0`，跳过总执行时限，仍执行启动期限、租约续期、取消与清理。不要把 `EXECUTION_TIMEOUT_SECONDS` 或 `LEASE_SECONDS` 改成 0 来代替此选项。

管理员通过 `EXECUTION_TIMEOUT_SECONDS` 调整上限，例如 `7200`，更新 API 与调度器的部署环境并重建对应服务。新提交按新上限校验；已提交任务及其重试保留客户端快照中的时限，不受后来降低上限影响。启动超时、心跳租约过期和客户端总执行时限已到会分别显示原因，资源仍需清理确认后释放。服务端长期运行时限不随本项调整。

有限时长配置不需要 Worker 升级；新增“不限”选项需要 API、调度器、前端及 Worker 0.19.0（协议 16）。不需要数据库迁移。旧 Worker 无法领取新不限配置；更新 API 不会自动更新节点 Worker。验证边界见 [可选执行限制](optional-execution-limits.md)。

### 执行上报与调度锁

运行中执行的续租按 Run → Slot 行锁更新，不获取全局调度锁，也不改写 Worker 配置 JSON；整机配置确认心跳仍锁定并刷新 Worker。旧日志、回合、旧产物和任务结果上报按执行行锁保护顺序、配额与幂等，互不串行化无关执行。首次启动转换、领取、完成和清理仍保留调度锁，不能直接删除资源状态转换的锁。

超时回收对疑似超时的执行取得行锁后重新读取租约，正在被其他事务更新的执行留待下个调度周期检查。此优化不延长租约，也不让已过期的执行恢复有效；Worker 看门狗到期仍停止业务。数据库完全不可用及同一执行自身的长事务仍可能影响续租。容量以实测记录为准，不能根据连接数推算最大 Worker 数。

优先级补充：优先保护 Worker 执行续租、Worker/Agent 心跳及服务探测会话和服务健康上报。服务探测会话使用严格路径匹配进入控制池；日志、产物、Web 和任务领取不借用此预留。大量领取轮询不能挤占控制连接；该隔离不保证数据库整体不可用时仍能续租。

`GET /healthz` 使用无数据库、无同步线程的固定异步存活响应，不受普通入口饱和影响，避免正常背压导致存活检查误失败；`/readyz` 仍检查数据库并受普通预算限制。


界面读取优化使用兼容的摘要参数，不更改调度、租约或资源预约。API 先于前端发布，无数据库迁移和节点升级要求；具体接口、隐藏页刷新及测试边界见 [批次监控](batch-monitoring.md#列表与详情读取优化)。回退前端仍可使用原完整接口。

批次监控频繁失败时，先区分 request_budget、connection_pool 与 lock_timeout。监控前端已采用5秒正常刷新、失败退避和隐藏标签页暂停；后端仅在请求内复用相同托管服务诊断。需同时更新API和前端，无Worker升级或新增迁移。操作说明见[批次监控](batch-monitoring.md)，持锁查询见[控制操作背压](control-operation-backpressure.md)。这些措施降低读取压力，不代表任意并发容量保证。

### 断联补报接收接口的边界（开发中）

`POST /api/v1/worker/executions/{execution_id}/recovery/control` 进入执行控制通道，`.../recovery/results` 进入可靠结果通道；四角色网关和旧双进程网关同步维护。请求使用 Worker 鉴权及原分配代次，旧执行没有显式恢复授权会返回 404，不会仅因调用新接口就升级策略。

响应中的 `digest` 表示消息已在数据库提交，`reconciled: false` 表示尚未完成生命周期对账。它不能刷新断联计时、证明业务成功或释放资源。重复同序号同内容幂等，异内容 409；收据大小/容量超限 413，节点应保留未确认消息。删除或撤销的执行不接收新的业务结果；在可信节点鉴权仍有效时，允许保留停止证据供后续安全核对。节点凭据被撤销仍返回 401，不能绕过节点鉴权。当前领取路径不发放该授权，Worker 执行循环也尚未启用新策略。

断联恢复默认值（开发中）：新协商执行的恢复授权默认显式保存 disconnect_tolerance_seconds=null，表示不限制断联时长；用户可选正整数秒。旧执行没有恢复策略字段时仍走旧协议，不能隐式启用。业务执行时限和已收到的取消独立生效。当前已实现基础模块、补报收据、控制对账与本地监督，完整领取/监督/结果恢复链路尚未启用，修改默认值不会改变现网执行。

调度恢复保护：scheduling.reconcile 对匹配执行 ID、Run ID、槽位的显式恢复授权，不再因旧租约或缺少启动上报将任务终结；撤销授权也不能充当进程已停止的证明。业务总执行时限仍独立生效，资源继续占用直至清理确认，旧协议及未领取预约维持原规则。批量读取使用相关 EXISTS，锁后重新核对授权与执行状态，不为每条执行增添独立授权查询。本次尚不向实际任务发放恢复授权，不能据此宣称端到端断联恢复已启用；回归见 test_recovery_scheduling.py。部署必须先完成 ae185f6 迁移。


### 控制状态对账（开发中，未启用完整恢复）

新增 `POST /api/v1/worker/executions/{execution_id}/recovery/reconcile`，按原执行、分配代次及宿主机 InvocationID / 容器完整 ID 核对状态；响应必须先在节点持久化，再用于本地控制。普通收据与重复响应不能延长断联容忍时间。平台已生效的取消、业务执行超时和终态优先；迟到退出码 0 或业务成功仅保留为事实，不能撤销平台决定。单纯心跳过期不能当作业务超时或清理成功。

有限容忍期内允许原执行恢复；到期后保持停止决定，迟到状态仍可核对，但不恢复运行、不释放资源。默认不限断联时长，业务执行时限仍独立有效。进程身份冲突转待核对，不向其他进程下发停止。失联服务恢复还需重做就绪检查。

本阶段仅实现控制对账 API 与节点持久决策模块，尚未接入实际 Worker 主循环、领取能力协商和结果应用。不要删除现有租约看门狗或手动创建生产恢复授权。数据库需先迁移至 `af296a7`；完整恢复链路上线前，现网仍使用旧执行协议。


### 节点本地监督模块验证（尚未启用）

恢复监督将本地进程观察与 HTTP 对账拆开：网络请求不持有本地监督锁，有限断联截止和业务执行时限仍可独立触发停止。记录包含原执行/分配代次、boot ID、systemd InvocationID 或完整容器 ID、单调计时、实际退出码和待重放请求；重启不重置起点，丢 ACK 重放原正文和序号。身份变化进入待核对，不按同名 unit 或容器杀掉另一个进程；停止失败保留原占用，下一次监督重试，进程消失不会伪造退出码。

本轮在隔离的本机 systemd 用户服务和 Docker 短程序上测试了模拟控制断网、旧心跳过期继续执行、有限容忍停止、默认不限正常结束，以及原身份向测试 API 恢复对账。时间推进由测试时钟注入，不是长时间断网或 GPU/NPU 模型评测，界面中不会新增评测批次。尚未接入生产 Worker 主循环、能力协商和可靠结果应用，不应手动开启或删除旧看门狗。新增节点模块不增加数据库迁移，后续 API 接线仍依赖 af296a7。

### 领取响应丢失恢复基础（尚未启用）

新增控制通道 `POST /api/v1/worker/slots/{slot_id}/recovery/claim`，按原 request_id 查询同一 Worker/Slot 的原执行快照。查询不拿调度全局锁、不续租、不重新分配资源；取消、超时、撤销、删除或执行身份变化时不返回启动配置。返回的 `launch_authorized=false` 表示这只是分配证据，必须另行对账，不能用它启动程序。

快照与分配授权须同事务持久化，重复请求不得重新渲染模板或更新执行起点。当前仅实现绑定工具与恢复接口，生产领取尚不调用；旧协议和生产看门狗不变。该阶段需要 b03a7b8 迁移，尚未部署，不能以接口测试通过宣称长期断联恢复已上线。

### 首次启动确认与崩溃窗口（开发阶段，未启用）

`POST /api/v1/worker/executions/{execution_id}/recovery/prepare` 属于执行控制通道，核对原领取请求、分配代次、Worker/Slot、机器启动身份及宿主机/容器模式；与取消按相同事务锁顺序串行。已取消、超时、撤销或身份变化不允许启动，重复请求不续租、不重置业务起点。首次确认最长 30 秒的有效窗口用于避免长时间滞留的启动许可，不是业务执行超时或断联容忍期限；重放不延长窗口，镜像/环境等耗时准备必须在请求前完成。

节点在调用启动命令前持久化一次性启动意图。命令异常或响应丢失不能证明没有启动；只能按原启动标识、boot ID 和 systemd InvocationID/完整容器 ID 找回实例，不能再次执行启动。找不到可信实例则保留待核对，不释放资源或自动重跑。取消在确认之后发生仍须后续控制对账传达，不能承诺断网时即时取消。

此阶段仅提供确认 API、节点启动账本和隔离真实运行测试。HostBackend 的服务 Description 与 Docker 创建 label 已接入可选启动标识，RecoveryRuntime 按原标识启动/检查实际实体；实际 Worker 主循环及结果清理仍待完成，不广告恢复能力，不改变现网行为。未新增迁移，依赖 af296a7/b03a7b8 的控制记录和领取快照表；尚未部署。

启动窗口还受原分配剩余断联容忍时间限制，可短于 1 秒；节点从原请求发出时刻计算，确认在截止点或之后到达即拒绝启动。重复请求保持原窗口，不增加容忍额度。

恢复结果应用（开发中）：`POST /api/v1/worker/executions/{execution_id}/recovery/results` 只有显式 `apply: true` 才将已授权原执行的回合事件/SDK 任务结果应用到业务记录；收据与结果在同一事务提交，返回 `reconciled: true` 后节点才确认交付。未携带该字段保留原收据语义。过期租约、已取消或业务超时不阻止合法迟到结果保存，但补报不改变平台生命周期、租约或清理状态；撤销、删除及归属冲突仍拒绝业务数据。不启用旧 Worker，不代表完成/清理或长期断联链路已上线。

恢复完成与清理（开发中）：现有 complete 请求可携带 recovery={generation, identity}，其中 identity 为原 boot ID 和 systemd InvocationID / Docker 完整 ID。只有平台已核对退出事实，才接收完成；该确认不释放资源，节点移除原运行实体后再向 cleanup 提交相同身份。相同完成正文可幂等重放，不同正文冲突；平台已接受取消或超时不被迟到成功覆盖。带恢复授权的执行禁止无身份证明的旧 complete/cleanup 请求。协议成功仍须通过现有结果完整性验证，尚未完成“结果未齐时独立释放”以及完整 Worker 主循环，不可视为上线。

恢复对账保留同一进程的已确认退出：相同退出可用新序号重报，退出后运行实体卸载不抹掉退出码；退出码冲突或声称重新运行进入待核对，不能据此完成/释放。此规则同样适用于平台已取消或超时后的上报。

取消后重新评测：原执行必须终结且 cleanup_confirmed=true 才能创建下一次运行；新的Run在调度时获得新的execution_id。旧执行的取消及迟到补报不得作用于新运行。恢复收尾专项覆盖旧完成/清理/对账在重新评测之后重放不改变新任务PENDING。

Worker 恢复收尾（开发中）：执行记录包含 recovery 身份及 recovery_finalization 阶段时，execute 优先恢复完成/移除/清理，不再经过启动流程。完成正文先落盘；收到有效完成确认才移除已确认退出的原运行实体；收到清理确认才清除 Worker 执行记录。任一步骤网络/落盘失败均保留原阶段，重放不重新启动；移除失败不发送清理确认。该入口仅接通收尾，正常领取、监督主循环及结果未齐收尾仍未全部接通。
