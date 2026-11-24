# 四角色 API 隔离

此部署将平台的一个外部 API 地址分流到四个处理进程。它不修改业务调度规则、数据库表结构或 Worker 协议。本机平台于 2026-11-25 已切换，13 个 Worker 卡位和 6 个 Agent 回连验证通过；其他部署仍需显式选用覆盖配置。发布验收状态和失败证据以 [验证记录](validation/priority-api-isolation-20261124/README.md) 为准。

| 进程角色 | 请求范围 | 默认数据库连接上限 |
| --- | --- | --- |
| `execution-control` | Worker 续租、服务健康、停止许可、完成及清理确认 | 执行控制 24 |
| `interaction` | 登录、用户提交与管理、向导预检、页面查询 | 用户操作 10、预检/长操作 4、页面读取 10 |
| `worker-data` | 任务领取、协议结果、Agent 采样与配置回执；唯一后台调度器 | 领取 6、结果 7、观测 8、调度 1 |
| `bulk-transfer` | 日志及历史产物上报、文件和视频字节流 | 批量传输 10 |

合计最多 80 个连接，每个池 `max_overflow=0`。这是初始预算，不是支持 100 个 Worker 的容量承诺。四进程仍共享 PostgreSQL CPU、磁盘、WAL 和行锁；同一行上的操作仍可能等待或返回可重试的繁忙响应。

## 配置与容量

只有四角色覆盖配置使用以下参数，单 API 与原双 API 保留原参数语义：

- `API_ROUTE_GROUP`：由覆盖文件指定角色；`API_DB_PROCESS_COUNT=4`。
- `DB_BACKPRESSURE_ENABLED=1`、`DB_ADMISSION_ENABLED=1`：四角色模式必需。类别许可在鉴权、数据库连接和同步线程分派前获取，最多等待 `DB_POOL_TIMEOUT_SECONDS`；等待取消后释放排队位置。日志和页面不能借用用户操作或执行控制的额度。
- `PRIORITY_API_POOL_SIZES`：可选 JSON，必须同时包含全部九类额度，所有进程使用同一份配置。例如 `{"execution_control":24,"user_action":10,"user_long":4,"page_read":10,"worker_claim":6,"worker_result":7,"worker_observation":8,"scheduler":1,"bulk_transfer":10}`。空值使用表中默认值。调度池必须为 1。
- `DB_CONNECTION_BUDGET`：所有四角色池之和的上限，默认 80；不能分别给每个进程分配 80。
- `DB_CONNECTION_HEADROOM`：为其他数据库客户端、管理员及 PostgreSQL 保留连接留出的总余量，默认 20。每个进程启动时读取实际 `max_connections` 和超级用户保留连接，预算加余量超过数据库上限即拒绝启动。此检查不预测其他应用未来占用；部署前仍需核对并配置实际余量。
- `DB_POOL_TIMEOUT_SECONDS` 默认 0.25，`DB_LOCK_TIMEOUT_SECONDS` 默认 0.5，短请求 SQL 总预算 `DB_REQUEST_TIMEOUT_SECONDS` 默认 4，必须小于 Worker 单次 HTTP 的 5 秒超时。
- `DB_LONG_REQUEST_TIMEOUT_SECONDS` 默认 30，范围大于 20 且不超过 60；用于等待 SSH 执行器等长操作，防止原 4 秒预算使外部等待后的数据库处理必然失败。这是 SQL 剩余预算，不是强制中止任意 Python 函数的计时器。
- `PRIORITY_API_PEERS`：仅自定义内部拓扑时使用，JSON 中必须包含全部四角色的 HTTP/HTTPS origin，禁止路径和凭据。普通 Compose 使用内部服务名，无需设置。

各进程的同步线程额度至少为 40，且不小于该角色全部 HTTP 准入额度之和加 4 个清理余量。连接池限制同时持有的连接数，准入限制同类在途请求数；两者不能换算为 Worker 数。依据实际峰值请求率、连接持有时间与尾部延迟调整额度，并保持总预算检查通过。

## 启动与检查

使用原环境文件、项目名、数据库卷和镜像。不要同时叠加 `split-api-compose.yml` 或把外部端口重新绑定到 `api`。从旧部署切换前应完成备份、排空并确认没有待确认的执行；停止旧 API 后再启动新角色，避免旧调度器继续运行。

```bash
docker compose --env-file .env -p mini-eval \
  -f deploy/compose.yml -f deploy/priority-api-compose.yml config --quiet

# 原双进程部署：在完成排空和备份后停止旧 API
docker compose --env-file .env -p mini-eval \
  -f deploy/compose.yml -f deploy/split-api-compose.yml stop api api-gateway control-api

docker compose --env-file .env -p mini-eval \
  -f deploy/compose.yml -f deploy/priority-api-compose.yml up -d --build --wait --remove-orphans

docker compose --env-file .env -p mini-eval \
  -f deploy/compose.yml -f deploy/priority-api-compose.yml ps
```

若原来是单 API，停止命令仅使用原配置中的 `api` 服务。若现场还有额外覆盖文件，保留原来的存储和纳管配置，并核对最终 `config`，不要删除数据卷。此功能无需新数据库迁移；跨越其他版本升级仍按[数据库迁移指南](database-migration.md)处理。

如果旧部署在额外覆盖文件中只给 `api` 设置了共享目录、`MINI_EVAL_SHARED_STORAGE` 或其他运行配置，**不能仅保留该文件就认为四角色都会继承**。三个新增角色继承的是基础 `compose.yml` 中的 `api`，不会自动复制旧覆盖文件的自定义字段。公共参数优先放到基础 Compose 支持的环境变量中；仍需额外覆盖的目录和配置，应明确应用到四个角色，并逐个核对最终 `config`。尤其 `bulk-transfer-api` 必须能只读访问原产物挂载，否则页面可打开但文件预览失败。

修改 `.env` 或覆盖文件后重新执行同一套 `up -d --wait`，由 Compose 重建配置变化的容器；只修改文件或执行 `docker restart` 不会加载新的容器环境。

外部绑定仍由 `API_BIND_HOST` / `API_BIND_PORT` 决定；原来使用额外私网入口的部署，应继续叠加 `split-api-lan-compose.yml` 并保留 `WORKER_BIND_HOST`，该文件也适用于四角色网关；业务程序与节点不需要更换 URL。`api` 服务承担 interaction 角色，其网络别名为 `interaction-api`。只有 `worker-data-api` 设置 `SCHEDULER_ENABLED=1`，不要额外扩容此服务副本。

外部 `/healthz` 由交互进程并发检查全部角色的存活与配置指纹。角色缺失、不可达、名称错误或预算配置不一致时返回失败；请求不会回退到其他角色。各容器自己的 `/healthz` 不访问数据库，返回角色和指纹；容器自己的 `/readyz` 会验证其数据库池，二者用途不同。`/readyz` 经公共地址只检查交互进程，全部角色就绪应逐个在对应容器中检查。

日志中 `category` 标识九类预算中的具体类别；503 的 `reason` 区分 `request_concurrency`、`connection_pool`、`lock_timeout`、`statement_timeout`、`request_budget` 等原因。错发到其他角色的请求在访问数据库前返回 404。

## 回退

排空后，用四角色配置停止 `api-gateway api execution-control-api worker-data-api bulk-transfer-api`，再按原单/双 API 覆盖文件启动当前版本镜像；使用 `--remove-orphans` 清理停用的 API 容器。保留数据库卷、共享存储和节点 journal，不降级数据库。双进程的 `api` 恢复为唯一调度器，`control-api` 不启动调度器。

## 路由与测试

方法和路径以 `backend/app/priority_route_manifest.json` 为唯一分类来源。运行 `python -m backend.app.priority_gateway > deploy/priority-api-nginx.conf` 更新网关；专项测试校验所有 FastAPI 路由和生成文件。新增 API 必须加入清单，不能通过自定义请求头提升优先级。

隔离测试工具支持 `--priority-api --mixed-ui`，以真实 HTTP/PostgreSQL 验证四角色、真实调度、用户提交/暂停/恢复/重评测、结果幂等、模拟 Worker 上报及日志。测试会停止批量进程，确认健康检查失败、执行心跳继续响应，随后重启批量进程。增加 `--continuous-batches` 可持续提交和重评测；`--log-bytes 16384 --varied-logs` 使用内容变化的日志单块上限（不加 varied-logs 的旧驱动正文为高度可压缩重复字符）；`--bulk-outage-seconds 15` 在开始 30 秒后暂停批量进程 15 秒，观察日志重试与恢复，仅允许四角色独立日志模式且时长至少为 60 秒加故障时长。允许的 502/504 仅限故障档日志/产物请求，控制请求错误仍判失败。测试只应使用可销毁 PostgreSQL；模拟程序生命周期不等于真实模型验收。

API 隔离尚不解决 Worker 长时间断联后的持续执行与对账，也不替代持久 outbox。当前本地租约 watchdog 和平台超期处理仍可能停止或结束任务；这些边界见 [API 隔离审计](validation/priority-api-isolation-20261124/README.md)。
