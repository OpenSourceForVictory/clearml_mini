# API 流量隔离现状审计（设计实施基线）

此记录基于本仓库现有代码生成，不代表四角色方案已上线。使用临时测试 JWT secret 导入 FastAPI 应用，逐条列出已注册方法、路径、处理函数、当前 `request_category`、依赖、鉴权方式、请求体类型及处理函数中直接出现的锁、提交和外部 I/O 标记。完整清单见 [registered-routes.tsv](registered-routes.tsv)。鉴权依赖计数：管理员 Bearer 68、用户 Bearer 63、节点 Bearer 29、媒体短期 Cookie 4、公开 3；媒体 Cookie 在处理函数内校验，不能仅看 FastAPI 依赖。标记仅来自处理函数源码，不自动追踪被调用 helper；不能用 `False` 证明没有事务或锁。

拟议类别已经写入 `backend/app/priority_route_manifest.json`，由 `backend/app/priority_routes.py` 按 HTTP 方法及完整路径匹配。同一清单的 `legacy_category` 保留既有单/双 API 分类；`db_budget.request_category` 现在从此字段读取，所以现有部署的入口、池和网关路由不变。`backend/tests/test_priority_routes.py` 逐个比对 FastAPI 注册表及变更前类别，漏掉新路由时测试失败；未知路径在新方案中只得到 `fallback`，不能借用高优先级类别。四角色类别尚未接入运行中的中间件或网关。`/suites/catalogs`、`/suites/manage` 与动态详情路由存在模式重叠，但类别相同；不同类别的重叠会拒绝分类。旧式与共享目录的产物媒体 GET/HEAD 均归批量传输。

`python -m backend.app.priority_gateway` 可由相同清单生成 [四角色 Nginx 候选配置](../../../deploy/priority-api-nginx.conf)，用 `$request_method:$uri` 区分 GET/HEAD/POST 等方法；静态路径先于动态路径，未知路径只转到交互进程。`test_priority_gateway.py` 检查生成结果与清单逐项一致；Nginx 1.27 `nginx -t` 已通过。此配置尚未连入 Compose 或部署：后端角色拒绝、角色就绪检查、健康检查聚合和回退配置未完成，不能单独启用。现有双进程网关保持原样。

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

本阶段做了仓库静态审计、FastAPI 注册路由枚举和四角色分类清单；既有分类改为从同一清单读取，逐路由回归验证类别不变。分类专项 `test_priority_routes.py`、既有双进程及数据库预算专项合计 129 通过、15 跳过（需独立 PostgreSQL）；新分类模块定向行/分支覆盖均为 100%，不代表全项目覆盖。OpenSpec 2.1 已完成；1.1 的逐路由鉴权、体积、事务与重试核对仍未完成。

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

这档尚未覆盖空闲 Worker 领取并完成新任务、结果 ACK、重新评测、长时间页面刷新、数据库 CPU/WAL/锁样本及真实业务程序；OpenSpec 1.2 因而仍未完成。它已经证明现有普通 API 容量下页面详情可被观察/批量流量挤出，下一步用完整混合场景量化四角色隔离的改善。现有运行路由和部署未因压测改变。
