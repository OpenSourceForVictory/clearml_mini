# API 流量隔离现状审计（设计实施基线）

此记录保留实施时的静态审计和测试基线；本机发布结果见末节，不能把历史基线当作当前部署。使用临时测试 JWT secret 导入 FastAPI 应用，逐条列出已注册方法、路径、处理函数、当前 `request_category`、依赖、鉴权方式、请求体类型及处理函数中直接出现的锁、提交和外部 I/O 标记。完整清单见 [registered-routes.tsv](registered-routes.tsv)。鉴权依赖计数：管理员 Bearer 68、用户 Bearer 63、节点 Bearer 29、媒体短期 Cookie 4、公开 3；媒体 Cookie 在处理函数内校验，不能仅看 FastAPI 依赖。标记仅来自处理函数源码，不自动追踪被调用 helper；不能用 `False` 证明没有事务或锁。

类别已经写入 `backend/app/priority_route_manifest.json`，由 `backend/app/priority_routes.py` 按 HTTP 方法及完整路径匹配。同一清单的 `legacy_category` 保留既有单/双 API 分类；`db_budget.request_category` 按此字段读取。四角色模式由新的 `priority_runtime.py` 使用目标类别；只在显式选择四角色时启用。`backend/tests/test_priority_routes.py` 逐个比对 FastAPI 注册表及变更前类别，漏掉新路由时测试失败；未知路径在新方案中只得到 `fallback`，使用交互进程的页面读取额度，不能借用高优先级类别。`/suites/catalogs`、`/suites/manage` 与动态详情路由存在模式重叠，但类别相同；不同类别的重叠会拒绝分类。旧式与共享目录的产物媒体 GET/HEAD 均归批量传输。

`python -m backend.app.priority_gateway` 由相同清单生成 [四角色 Nginx 配置](../../../deploy/priority-api-nginx.conf)，用 `$request_method:$uri` 区分 GET/HEAD/POST 等方法；静态路径先于动态路径，未知路径只转到交互进程。`test_priority_gateway.py` 检查生成结果与清单逐项一致；Nginx 1.27 `nginx -t` 已通过。四角色运行时已接入角色拒绝、角色数据库就绪检查、配置指纹与聚合健康检查，新增可选 `priority-api-compose.yml`；隔离四进程实测通过，随后已完成本机切换，见末节。部署与回退见[操作指南](../../priority-api-isolation.md)。

全路由声明的请求体模型、字段限制、目标角色和确认类别见 [request-contracts.json](request-contracts.json)，可用 `python -m backend.tests.priority_route_audit` 重建；它和 TSV 共同提供逐路由入口索引。JSON Schema 不包含所有自定义 validator：业务参数的 16 KiB、日志 UTF-8 字节数及响应大小等必须结合下表和处理器源码。未声明请求体不等于请求没有成本；网关没有统一字节上限，不能把字段校验当作传输层大小限制。

## 规模与当前拥塞面

167 条方法路由（含 `/healthz`、`/readyz` 和媒体 HEAD）：GET 62、POST 80、DELETE 4、PATCH 7、PUT 12、HEAD 2。当前分类为 ordinary 152、control 12、observation 1、result 2；除 12 条 control 外均由双 API 部署中的普通进程处理。直接源码中 75 条包含锁字样、85 条包含 `s.commit()`、12 条显式调用本机执行器或共享文件 I/O；这些计数是审计线索，不是完整调用图。

| 路径 / 操作 | 现有入口 | 实际语义与事务边界 | 目标类别 |
| --- | --- | --- | --- |
| `POST /worker/heartbeat` | 控制进程、control 池 | 认证后按执行取 Run、锁 Slot、提交续租；STARTING 转 RUNNING 时还尝试调度锁；响应含取消及期望配置 | 执行控制 |
| `POST /worker/executions/{id}/service-health`、`service-probe-session` | 控制进程 | 服务就绪状态影响新客户端是否可绑定；只接受有效执行租约 | 执行控制 |
| `POST /worker/executions/{id}/complete`、`cleanup` | 控制进程 | 调度锁、每执行日志锁、终态/资源清理提交后 ACK；重试要核对原执行身份 | 执行控制 |
| `POST /batches`、`POST /orchestrations` | 普通进程、ordinary 池 | 全局调度锁 + 用户行锁；批量持久化后提交，再组装响应；编排提交有强制幂等键 | 用户写入 |
| `POST /batches/{id}/control`、`retry-failed`、`POST /jobs/{id}/retry` | 控制进程 | 用户操作会进入调度锁/批量任务处理；不宜与 Worker 续租共用处理容量 | 用户写入 |
| `PATCH /batches/{id}/priority`、管理 `PUT/PATCH/DELETE` | 普通进程 | 用户管理/调度写入，当前非 POST 一律 ordinary；需要审计修订号、鉴权与事务确认 | 用户写入 |
| `POST /batches/preflight`、`POST /orchestrations/preflight` | 普通进程 | 用户主动触发、只读但可能计算资源匹配；不能占满提交额度 | 用户预检/长操作 |
| `GET /admin/ssh-onboarding` 及纳管检查 | 普通进程 | 调用本机执行器，最长约 20 秒；部分路径在外部 I/O 前 `s.close()`，仍占同步线程 | 用户预检/长操作 |
| `POST /worker/slots/{id}/claim` | 普通进程 | 无候选先只读返回；有候选才尝试调度锁、重查并转 STARTING，提交后下发同一执行 ID | Worker 领取 |
| `POST /agent/heartbeat` | 普通进程、observation 池 | 更新 AgentObservation，含采样、配置 ACK、程序事件及设备资源；同一节点行锁，提交后返回事件 ACK | Worker 观测/确认 |
| `POST /worker/executions/{id}/episodes`、`task-result` | 普通进程、result 池 | 按执行身份写协议回合/任务结果；冲突不得覆盖，ACK 后 Worker 推进游标 | 可靠结果 |
| `POST /worker/executions/{id}/logs-v2` | 普通进程、ordinary 池 | 单块 UTF-8 ≤16 KiB、每执行窗口 256 KiB；Run KEY SHARE + 执行专属日志锁，序号 ACK、超限显式截断 | 批量传输 |
| 旧 `logs`、`artifacts`、`log-catalog`、Worker 日志回读 | 普通进程、ordinary 池 | 正文/元数据分别有现有配额与身份校验；必须按各接口重试语义处理 | 批量传输 |
| `GET /jobs/{id}/logs`、批次详情与监控 | 普通进程、ordinary 池 | 用户读取；页面自动刷新与主动查看共用路径，无法从客户端自称优先级 | 页面读取 |
| `GET/HEAD .../shared-artifacts/media/{id}` | 普通进程、ordinary 池 | 短期 cookie 授权、范围校验；数据库会话在共享存储传输前关闭，但连接仍占 API 处理链路 | 批量传输 |

## 容量与确认边界

可选双进程当前连接池普通 23 + 观察 9 + 结果 7 + 调度 1，控制 40，共 80；单进程默认配置保持独立。此处的 80 是 API 池的最大可能占用，不是实测 Worker 上限；实际 PostgreSQL `max_connections` 需要现场核对。`request_category` 对非 POST 一律 ordinary；网关手写控制路径。四角色实施必须从同一分类清单派生入口与池，并在隔离 PostgreSQL 上加入用户提交、页面查询、真实调度、完成清理及大日志，不能仅重跑原 100 Worker 模拟脚本。

当前 Worker 本地租约 watchdog 仍会在持续续租失败时停止已启动程序；平台在租约到期外加 `LOST_WORKER_GRACE_SECONDS` 后将执行标为 TIMED_OUT 并隔离卡位。四角色隔离只能降低过载导致的心跳失败概率，不能替代 `worker-control-plane-backpressure` 未完成的断联继续执行与对账工作。日志 v2 有单块重试和序号确认，旧上报与持久 outbox 的边界不同，不可笼统称为“日志永不丢”。

## 后台与节点出站请求

FastAPI 路由清单不能代替客户端调用清单。后台调度在 `main.lifespan` 中仅由 `SCHEDULER_ENABLED=1` 的进程启动，`scheduling.loop` 默认每 2 秒执行一次 `tick`；它直接使用 scheduler Session，不走 HTTP 中间件和网关。四角色模式必须确保仅 worker-data 角色启用它。下表列出当前节点侧调用族；具体路径也都能在注册路由清单中找到。

| 调用者 | 方法和路径族 | 本地重试或确认边界 | 分类注意点 |
| --- | --- | --- | --- |
| Worker 主循环 | POST `/worker/register`、`/worker/heartbeat`、`/worker/slots/{id}/claim` | 每次 HTTP 尝试超时 5 秒；注册/领取按循环重试。执行心跳只在收到有效回复后延长本地租约，过期会触发 watchdog 停止单元；领取须以执行 ID 和本地 journal 避免重复启动 | 注册和领取不应占用执行续租预留；心跳含取消/配置回执，不能归可丢遥测 |
| Worker 执行控制 | POST `/worker/executions/{id}/service-health`、`service-probe-session`、`complete`、`cleanup` | 完成/清理在平台提交后 ACK；本地 journal 保留失败/恢复标记，不能把 HTTP 超时当成未提交证明 | 必须保留执行身份、锁顺序和清理确认 |
| Worker 可靠结果 | POST `/worker/executions/{id}/episodes`、`task-result` | 结果按原执行身份上报；暂时失败保留待重试事件，重复上报由平台按原协议校验；当前尚无完整持久 outbox | 不与日志字节传输共池 |
| Worker 批量与历史日志 | POST `/worker/executions/{id}/logs`、`logs-v2`、`artifacts`、`log-catalog`、`shared-artifacts`；POST `/worker/log-reads/claim`、`/worker/log-reads/{id}/reply` | v2 日志按序号/摘要 ACK 并限制单块；日志目录及历史读取有本地状态和失败重试，但不等于所有数据都可靠补传 | 上传字节与元数据更新、历史读取命令应分别校验锁及响应大小 |
| Worker 保留期与策略 | POST `/worker/slots/{id}/execution-data/{suffix}`、`/worker/slots/{id}/log-policy` | 定期轮询和回执；暂时失败延后，不应让维护循环拖住执行心跳 | 配置回执不可视作可丢样本 |
| Node Agent | POST `/agent/register`、`heartbeat`、`stop-permit` | 独立 HTTP 调用超时 5 秒；heartbeat 与 stop-permit 使用不同本地并发 lane；401/409 表示会话失效 | heartbeat 混合采样、设备、配置 ACK 与事件，不能整包丢弃；stop-permit 是控制 |
| 节点 Updater | POST `/worker/upgrades/poll`、`report`；GET `/worker/upgrades/{id}/archive`、`state` | 每次请求超时 10 秒；下载归档最多 2 MiB，升级 journal 支持恢复与状态核对 | poll/归档与执行续租分预算；report 是升级确认 |

用户浏览器请求仍由注册路由清单覆盖。页面自动刷新和用户主动查看若访问同一个 GET，当前 API 无法识别意图；不能仅凭请求头赋予不同优先级。清单中的 `http_body_limit=none_at_gateway` 表示网关未施加统一字节上限，**不表示**处理器无限接收：例如日志 v2 的 `content` 上限 16 KiB、旧日志单块上限 8192 字符、Updater 归档响应上限 2 MiB、Agent 响应上限 128 KiB；其他端点仍需逐个从 Pydantic 模型及处理器核对。

## 验证边界

本阶段做了仓库静态审计、FastAPI 注册路由枚举和四角色分类清单；既有分类改为从同一清单读取，逐路由回归验证类别不变。分类专项 `test_priority_routes.py`、既有双进程及数据库预算专项合计 129 通过、15 跳过（需独立 PostgreSQL）；新分类模块定向行/分支覆盖均为 100%，不代表全项目覆盖。这是分类阶段的历史结果；后续新增请求体契约导出、四角色运行时及真实 PostgreSQL 验证，见下文。

### 双进程混合基线第一档

`backend/tests/control_plane_load.py --mixed-ui` 现在可在隔离 PostgreSQL 中启用真实调度线程，并用真实用户 JWT 提交一个普通命令批次、暂停/恢复调度、每 2 秒查询批次列表与详情。其他 100 个模拟 Worker 持续续租、领取、服务健康、产物上报、独立日志流和 Agent 采样。运行示例：

```bash
LOG_TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:5432/postgres' \
  .venv/bin/python -m backend.tests.control_plane_load \
  --grades 100 --seconds 30 --output /tmp/priority-api-mixed \
  --split-api --role-aware-pools --agent-telemetry --independent-logs \
  --mixed-ui --ordinary-pool 23 --control-pool 40 \
  --observation-pool 9 --result-pool 7 --progress-every-30s
```

只对可销毁 PostgreSQL 实例使用此命令；脚本为每档创建并删除独立数据库。2026-11-24 本机第一次 100 Worker/30 秒运行全部通过，第二次保留的失败证据见 [mixed-baseline/summary.json](mixed-baseline/summary.json) 和 [进度记录](mixed-baseline/progress-100.jsonl)：8077 个 200、1 个 201、120 个 503；其中批次详情 1 次 `request_concurrency` 503，Agent 41、日志 24、产物 25、领取 29 次同类拒绝，执行心跳无拒绝。1500 条日志全部 ACK，最大日志积压 1.28 秒，最大心跳间隔 3.34 秒，租约过期数 0；隔离数据库测试时 `max_connections=100`，结束时活动连接 52。批次最后仍为 `PENDING`，因为模拟 Worker 都已占满卡位。此失败不是服务实例或真实模型评测故障，也不能证明长期稳定。

**此档有配置偏差：它开启了入口准入，而现有双进程 Compose 默认关闭该开关。** 因此其 `request_concurrency` 失败不能直接代表默认部署。它也没有空闲 Worker 完成新任务、结果 ACK 或重评测，不作为完整混合基线。

### 修正后的完整混合基线与四角色观察

后续驱动保留总数 100 个 Worker，其中 99 个模拟持续运行的服务，1 个经真实调度器领取任务。浏览器使用独立 HTTP 客户端，每个节点的心跳、日志、Agent 可并行传输，连接池不再成为模拟多进程节点的额外串行点。客户端仅模拟业务退出和 SDK 任务结果；没有执行模型。真实 HTTP 流程包含提交、暂停/恢复、首次失败、两次同内容结果确认、用户重评测、新执行 ID 成功及页面监控；活跃租约统计排除终态执行。每 10 秒采样数据库连接/等待、未授予锁、事务与 WAL，并用 `docker stats` 记录隔离 PostgreSQL 容器 CPU、内存、块 I/O。这些是周期样本，不证明采样间没有短暂等待。

- [baseline-v2-100/summary.json](baseline-v2-100/summary.json)：保留的早期失败档。默认双进程关闭准入，但驱动仍共用 HTTP 客户端，监控 2 次连接池 503、心跳间隔 17.45 秒。其 `expired_leases=2` 错误计入已结束的两次执行，不能当作活跃租约失败证据。
- [baseline-v3-100/summary.json](baseline-v3-100/summary.json)：修正驱动后的默认双进程 60 秒对照通过。用户操作和页面无错误，活跃租约过期 0，2970 条日志全部 ACK；98 次连接池 503 分布于 Agent/领取/日志/产物。最长心跳间隔 3.55 秒，日志最大积压年龄 2.01 秒。
- [priority-v3-100/summary.json](priority-v3-100/summary.json)：同一驱动、四角色 180 秒通过；48186 个 200、1 个 201，无 503，8910 条日志全部 ACK，日志最大积压年龄 0.073 秒。停止批量进程时网关健康 503、批量请求 502、心跳 200，重启后健康恢复 200；全部角色 `/readyz` 200，误路由 404。

双进程对照命令必须带 `--split-api --role-aware-pools --no-admission --ordinary-pool 23 --control-pool 40 --observation-pool 9 --result-pool 7`；四角色使用 `--priority-api`。两者均使用 `--mixed-ui --client-per-worker --agent-telemetry --independent-logs --trace-http-phases --progress-every-30s`，数据库容器统计可加 `--postgres-container <隔离容器名>`。`--continuous-batches` 会在观察期间持续创建批次，每批次经过失败、重评测和成功，保留最后 15 秒收尾。持续批次档结果见下文；不能据单机模拟负载承诺生产容量上限。

### 100 Worker 持续批次混合负载（600 秒）

[完整摘要](priority-continuous-100/summary.json)与[每 30 秒记录](priority-continuous-100/progress-100.jsonl)保留本次运行证据。使用上面的四角色参数，追加 `--continuous-batches --seconds 600`，没有同时运行全量测试或镜像构建。

- 94 个批次均完成首次失败、重新评测和成功，188 个执行完成，重复结果确认 188 次；暂停/恢复共 188 次。真实调度器分配新执行 ID，模拟 Worker 回报结果，没有启动真实模型。
- HTTP 161266 个 200、94 个 201，无 503。心跳 p99 63 ms、领取 22 ms、批次监控 145 ms、用户控制 60 ms、提交 366 ms、重新评测 305 ms。
- 29692 条日志全部确认，最终积压 0，单 Worker 最多积压 1 块，最大积压年龄 77 ms；600 秒停止采样瞬间尚有 4 块在途，随后全部确认，不能将最后一行 progress 当作最终未完成数。
- 活跃租约过期 0、遥测过期 0；负载期间最大心跳间隔 2.20 秒，最终停止发请求后采样新鲜度为 4.08 秒。两者测量时点不同。
- 56 个数据库样本中连接峰值 51（数据库上限 100）、未授予锁 0、死锁 0；WAL 增量约 94.7 MB，隔离 PostgreSQL CPU 样本峰值 54.69%。周期采样不排除采样间短等待，事务回滚计数包含正常只读会话关闭，不等于失败请求数。
- 主动停止 bulk-transfer 后，公共健康检查 503、日志请求 502，执行心跳仍为 200；重启后健康恢复 200。四角色各自就绪 200，误路由 404。

这验证了当前模拟工作量下没有随时间增长的日志积压或控制通道异常，不能证明任意日志量、任务规模及持续数据库故障均可承受。默认池预算暂不调整；断联继续运行和持久 outbox 仍是独立未完工作。

### 专项回归

真实 PostgreSQL 下运行 `test_priority_runtime.py`、`test_priority_routes.py`、`test_control_plane_metrics.py`、`test_control_plane_load_gateway.py`、`test_db_budget.py`：156 通过。包括真实应用四角色生命周期、唯一调度线程、角色池隔离、锁超时回滚、配置漂移、排队取消及全部路由分类。

前端 TypeScript 检查和 `frontend/tests/user-guide.spec.ts` 4 项通过，覆盖管理员运维说明、普通用户隐藏及只读帮助操作。前端未插桩，不计入 Python 覆盖率。

### 容器构建、启动与回退

`backend/Dockerfile` 新构建测试镜像成功（包含前端构建）。独立 Compose 项目使用全新数据库和临时凭据，先按四角色覆盖启动，再停止旧角色并回退到双进程，最后回退到单 API；三个阶段均完成同一外部端口的健康、就绪、账号登录及当前用户查询，见 [compose-compatibility.json](compose-compatibility.json)。迁移和 bootstrap 成功，四角色公共健康响应包含全部四个正常角色。该验证没有更换生产镜像标签、修改生产数据库或重启现有节点。

### 事务与并发确认

追加真实 PostgreSQL 专项：批次暂停/取消与领取/调度/重试竞争、编排提交竞争、调度并发、连接池耗尽后的用户控制、日志与心跳锁竞争、任务结果契约，33 项通过。运行文件为 `integration/test_batch_controls_race.py`、`integration/test_orchestration_race.py`、`integration/test_scheduler_concurrency.py`、`test_control_operations_postgres.py`、`test_execution_logs_postgres.py`、`test_task_result_reporting.py`。这些数据库集成测试单独配置已迁移的 `TEST_DATABASE_URL`；它们的独立运行未计入全量覆盖率文件。

源码核对：`batches.create` 保留调度锁→用户行锁，提交后生成响应；控制操作保留修订号和调度锁；领取空队列只读、有候选才尝试锁并重查；完成保留调度锁→执行日志锁和提交后 ACK，重复完成仅接受匹配终态与已清理状态。此次未修改上述处理器、预约顺序或结果语义。连接池隔离不消除同一行的竞争。

600 秒长测日志块为 1 KiB、每 2 秒一块；单块上限 16 KiB 的高字节量及中断恢复档见下文；不能把前者当作日志上限容量验证。

早期摘要顶层 `executions=100` 沿用了 Worker 数量，不能作为持续批次的执行总数；实际新执行明细在 `ui_evidence.executions`（600 秒档为 188 次）。驱动后续改为查询执行表计数，历史证据原样保留。

新模块定向覆盖（77 项测试）见 [targeted-coverage.json](targeted-coverage.json)：`priority_runtime.py`、`priority_routes.py` 行/分支均 100%；`priority_gateway.py` 行 95%、分支 83.33%。未覆盖生成器的命令行入口；已独立执行生成命令及 Nginx 配置检查，但独立进程未合并覆盖率，不能计为覆盖。全项目门槛另行统计。

### 全量 Python 测试与覆盖率

按仓库要求执行 `python -m scripts.test_coverage`：**2790 通过、155 跳过、无失败**，耗时 2232 秒；测试退出码 0，覆盖率门槛通过。证据见 [测试及逐项跳过原因](full-suite.json)、[覆盖率表](full-coverage-summary.md)与[统计 JSON](full-coverage-summary.json)。完整覆盖率原始数据、HTML 和 JUnit 位于本机 `coverage-artifacts/priority-api-isolation/`。

整体行覆盖率 92.26%、分支覆盖率 84.15%；调度与资源 98.72% / 92.48%，节点纳管与升级 95.67% / 83.65%，业务执行与结果 96.12% / 88.96%。各目录均达到原有门槛，没有降低要求或排除未测生产代码。前端与未插桩独立进程不计入。

跳过项要求独立迁移数据库、真实 systemd/特权挂载、专用 Docker 镜像或旧 API checkout 等 opt-in 条件；不能视作通过。其中与本变更相关的数据库事务竞争已另配迁移后的 PostgreSQL 做前述 33 项验证，但该独立进程没有合并进全量覆盖率。全量开始后新增的故障注入工具测试另跑 25 项通过（包含正常、取消、恢复及错误分类边界）；测试辅助工具不属于生产覆盖率分母。

### 16 KiB 日志与中途故障

[重复字符故障档](priority-large-outage-100/summary.json)：100 Worker / 180 秒，运行约 31 秒时停止批量进程，46 秒重启；日志/产物收到 1994 次预期 502，恢复突发产生 160 次 `request_concurrency` 503（日志 120、产物 40），执行心跳、Agent、用户操作与页面无此错误。8910 块日志全部确认，最大积压 11 块/Worker、最大年龄 21.21 秒，最终积压 0；29 个批次重评测成功，活跃租约及遥测过期均为 0。

该档仍沿用旧驱动的重复字符正文，压缩率很高，不能代表较难压缩日志的数据库写入压力。为排除此偏差，驱动增加 `--varied-logs`，生成逐 Worker、序号和行变化的结构化文本；重试正文保持确定性，不改变 ACK 校验。辅助测试验证字节长度、重试一致性和压缩率边界，新增工具专项合计 28 项通过。它仍是合成日志，不是真实业务日志或模型评测。

[变化正文故障档](priority-varied-outage-100/summary.json)：同为 100 Worker / 180 秒、16 KiB/块、15 秒中断。26 个批次完成失败→重评测→成功；8910 块日志全部确认，最大积压 11 块/Worker、年龄 21.85 秒，最终 0。1982 次预期 502 和 165 次恢复突发 503 均仅出现在日志/产物，心跳、Agent 和用户操作无此错误；活跃租约/遥测过期 0。成功请求 p99：心跳 72 ms、用户控制 14 ms、监控 185 ms、日志 231 ms。数据库样本连接峰值 44、未授予锁峰值 0，WAL 增量约 125.1 MB（重复字符档 22.7 MB），CPU 样本峰值 60.26%。这排除了重复字符过度压缩的主要偏差，仍不等于任意真实业务量的容量保证。

原始 API 诊断日志和临时网关配置保留于本机 `.runtime/priority-api-isolation-load-20261125/`；Git 保留摘要、进度、失败档及复现参数，不提交运行目录。

### 本机发布（2026-11-25）

代码提交 `51fe692`，镜像 `mini-eval-api:priority-api-20261125` 已部署到原 `mini-eval` 项目，见 [发布验收](deployment.json)。发布前活动执行及待清理执行均为 0，已生成并检查 PostgreSQL 自定义格式备份；先停止旧 API/调度器，再启动四角色，未并行运行两个调度器。数据库仍为 `ad074e5`，没有新增迁移，没有更新节点组件。

原有两个网关地址、认证配置、数据库连接和共享产物挂载均保留；四角色都挂载原 API 的自定义产物目录。聚合健康正常，原登录会话、批次列表和机器列表均返回 200。原先在线的 13 个 Worker 卡位及 6 个 Agent 均在新网关就绪后再次上报（逐 ID 核对时间推进），没有仅用“容器 healthy”替代节点验收。

本次生产验证范围为控制端部署、真实节点回连及用户会话/读取；没有重新启动真实 GPU/NPU 模型程序，也未执行用户批次控制操作。批次提交、暂停/恢复、重评测及结果幂等由隔离真实 PostgreSQL/HTTP 专项和模拟 Worker 长测验证。长期断联继续执行、持久 outbox 和完成摘要仍属于其他未完成工作，本次发布不改变租约 watchdog 行为。

本机的环境文件、原/新覆盖文件清单、数据库备份及回退日志保留于受保护的 `.runtime/priority-api-release-20261125/`，不进入 Git；登录测试令牌已从该发布目录清除。
