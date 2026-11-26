# 四角色 API 隔离

此部署将平台的一个外部 API 地址分流到四类角色。 当前本机已采用12进程百人专项配置，见文末“百人团队候选配置与升级”和[本次报告](validation/team-capacity-20261126/README.md)；下表仍为不填写新增参数时的兼容默认值。新覆盖配置启动五个处理进程，其中执行控制有两个副本；旧四进程配置保持兼容。它不修改业务调度规则或 Worker 协议。本机平台于 2026-11-26 切换到五进程配置，发布状态与验证边界见[容量验证](validation/disconnected-execution-20261124/control-scale-20261126.md)。本次镜像同时包含独立的恢复结果字段迁移 `c14d09e`；仅调整四角色副本数本身不需要新表结构，跨版本升级仍应先检查迁移头。

| 进程角色 | 请求范围 | 默认数据库连接上限 |
| --- | --- | --- |
| `execution-control`（2 个副本） | Worker 续租、服务健康、停止许可、完成及清理确认 | 每副本 12，合计 24 |
| `interaction` | 登录、用户提交与管理、向导预检、页面查询 | 用户操作 10、预检/长操作 4、页面读取 10 |
| `worker-data` | 任务领取、协议结果、Agent 采样与配置回执；唯一后台调度器 | 领取 6、结果 7、观测 8、调度 1 |
| `bulk-transfer` | 日志及历史产物上报、文件和视频字节流 | 批量传输 10 |

合计最多 80 个连接，每个池 `max_overflow=0`。两个执行控制副本仍共享 PostgreSQL CPU、磁盘、WAL 和行锁；同一行上的操作仍可能等待或返回可重试的繁忙响应。200 Worker 的隔离 60 秒档通过不代表生产容量或长时间故障档通过。

## 配置与容量

只有四角色覆盖配置使用以下参数，单 API 与原双 API 保留原参数语义：

- `API_ROUTE_GROUP`：由覆盖文件指定角色；新覆盖配置使用 `API_DB_PROCESS_COUNT=5`、`API_CONTROL_REPLICAS=2`。旧四进程部署不设置后者，继续使用每类原额度。
- `DB_BACKPRESSURE_ENABLED=1`、`DB_ADMISSION_ENABLED=1`：四角色模式必需。类别许可在鉴权、数据库连接和同步线程分派前获取，最多等待 `DB_POOL_TIMEOUT_SECONDS`；等待取消后释放排队位置。日志和页面不能借用用户操作或执行控制的额度。
- `PRIORITY_API_POOL_SIZES`：可选 JSON，必须同时包含全部九类额度，所有进程使用同一份配置。新五进程配置为空时使用 `{"execution_control":12,"user_action":10,"user_long":4,"page_read":10,"worker_claim":6,"worker_result":7,"worker_observation":8,"scheduler":1,"bulk_transfer":10}`；执行控制的 12 须按两个副本计入总预算。旧四进程配置为空时仍使用执行控制 24。调度池必须为 1。
- `DB_CONNECTION_BUDGET`：所有实际进程的池上限之和，默认 80；不能分别给每个进程分配 80。
- `DB_CONNECTION_HEADROOM`：为其他数据库客户端、管理员及 PostgreSQL 保留连接留出的总余量，默认 20。每个进程启动时读取实际 `max_connections` 和超级用户保留连接，预算加余量超过数据库上限即拒绝启动。此检查不预测其他应用未来占用；部署前仍需核对并配置实际余量。
- `DB_POOL_TIMEOUT_SECONDS` 默认 0.25，`DB_LOCK_TIMEOUT_SECONDS` 默认 0.5，短请求 SQL 总预算 `DB_REQUEST_TIMEOUT_SECONDS` 默认 4，必须小于 Worker 单次 HTTP 的 5 秒超时。
- `DB_LONG_REQUEST_TIMEOUT_SECONDS` 默认 30，范围大于 20 且不超过 60；用于等待 SSH 执行器等长操作，防止原 4 秒预算使外部等待后的数据库处理必然失败。这是 SQL 剩余预算，不是强制中止任意 Python 函数的计时器。
- `PRIORITY_API_PEERS`：仅自定义内部拓扑时使用，JSON 中必须包含全部四角色的 HTTP/HTTPS origin，禁止路径和凭据。普通 Compose 使用内部服务名，无需设置。

各进程的同步线程额度至少为 40，且不小于该进程全部 HTTP 准入额度之和加 4 个清理余量。连接池限制同时持有的连接数，准入限制同类在途请求数；两者不能换算为 Worker 数。`execution-control-api` 两副本通过 Docker DNS 和网关分担请求；调度器仍只有 `worker-data-api` 一个。依据实际峰值请求率、连接持有时间与尾部延迟调整额度，并保持总预算检查通过。

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

新配置应显示两个 `execution-control-api` 副本及各一个交互、Worker 数据、批量进程。若旧 `.env` 显式设置 `PRIORITY_API_POOL_SIZES` 为旧的执行控制 24，启动会因五进程总连接预算超限而拒绝；先移除该旧覆盖值或改为上面的每副本 12，并通过 `config --quiet`。更新覆盖文件后须重建网关及 API，不可只重启现有容器。其他平台不会因本机代码提交或部署自动切换，升级前仍须排空与备份。

纳管执行器同时读取交互接口和安装包下载接口，后者属于批量进程。四角色覆盖文件将其内部控制地址设为 `http://api-gateway:8000`；更新覆盖文件后需重建 `onboarding` 容器。旧容器若仍直连 `http://api:8000`，在 SSH 检查阶段下载安装包会收到 `control_api_rejected_404`。排查时核对 `docker compose ... config` 中 `onboarding.command` 的 `--api-url`，以及 `/api/v1/admin/node-installation-bundle` 经网关是否返回鉴权响应；不要把未带凭据时的 401 误判成路由失败。

若原来是单 API，停止命令仅使用原配置中的 `api` 服务。若现场还有额外覆盖文件，保留原来的存储和纳管配置，并核对最终 `config`，不要删除数据卷。此功能无需新数据库迁移；跨越其他版本升级仍按[数据库迁移指南](database-migration.md)处理。

如果旧部署在额外覆盖文件中只给 `api` 设置了共享目录、`MINI_EVAL_SHARED_STORAGE` 或其他运行配置，**不能仅保留该文件就认为四角色都会继承**。三个新增角色继承的是基础 `compose.yml` 中的 `api`，不会自动复制旧覆盖文件的自定义字段。公共参数优先放到基础 Compose 支持的环境变量中；仍需额外覆盖的目录和配置，应明确应用到四个角色，并逐个核对最终 `config`。尤其 `bulk-transfer-api` 必须能只读访问原产物挂载，否则页面可打开但文件预览失败。

修改 `.env` 或覆盖文件后重新执行同一套 `up -d --wait`，由 Compose 重建配置变化的容器；只修改文件或执行 `docker restart` 不会加载新的容器环境。

外部绑定仍由 `API_BIND_HOST` / `API_BIND_PORT` 决定；原来使用额外私网入口的部署，应继续叠加 `split-api-lan-compose.yml` 并保留 `WORKER_BIND_HOST`，该文件也适用于四角色网关；业务程序与节点不需要更换 URL。`api` 服务承担 interaction 角色，其网络别名为 `interaction-api`。只有 `worker-data-api` 设置 `SCHEDULER_ENABLED=1`，不要额外扩容此服务副本。

外部 `/healthz` 由交互进程并发检查全部角色的存活与配置指纹。角色缺失、不可达、名称错误或预算配置不一致时返回失败；请求不会回退到其他角色。它按角色探测，**不能证明两个执行控制副本都在运行**；用 `docker compose ... ps` 核对副本数及各自健康状态。各容器自己的 `/healthz` 不访问数据库，返回角色和指纹；容器自己的 `/readyz` 会验证其数据库池，二者用途不同。`/readyz` 经公共地址只检查交互进程，全部角色就绪应逐个在对应容器中检查。

日志中 `category` 标识九类预算中的具体类别；503 的 `reason` 区分 `request_concurrency`、`connection_pool`、`lock_timeout`、`statement_timeout`、`request_budget` 等原因。错发到其他角色的请求在访问数据库前返回 404。

## 回退

排空后，用四角色配置停止 `api-gateway api execution-control-api worker-data-api bulk-transfer-api`，再按原单/双 API 覆盖文件启动当前版本镜像；使用 `--remove-orphans` 清理停用的 API 容器。保留数据库卷、共享存储和节点 journal，不降级数据库。双进程的 `api` 恢复为唯一调度器，`control-api` 不启动调度器。

## 路由与测试

方法和路径以 `backend/app/priority_route_manifest.json` 为唯一分类来源。运行 `python -m backend.app.priority_gateway > deploy/priority-api-nginx.conf` 更新网关；专项测试校验所有 FastAPI 路由和生成文件。新增 API 必须加入清单，不能通过自定义请求头提升优先级。

隔离测试工具支持 `--priority-api --mixed-ui`，以真实 HTTP/PostgreSQL 验证四角色、真实调度、用户提交/暂停/恢复/重评测、结果幂等、模拟 Worker 上报及日志。测试会停止批量进程，确认健康检查失败、执行心跳继续响应，随后重启批量进程。增加 `--continuous-batches` 可持续提交和重评测；`--log-bytes 16384 --varied-logs` 使用内容变化的日志单块上限（不加 varied-logs 的旧驱动正文为高度可压缩重复字符）；`--bulk-outage-seconds 15` 在开始 30 秒后暂停批量进程 15 秒，观察日志重试与恢复，仅允许四角色独立日志模式且时长至少为 60 秒加故障时长。允许的 502/504 仅限故障档日志/产物请求，控制请求错误仍判失败。测试只应使用可销毁 PostgreSQL；模拟程序生命周期不等于真实模型验收。

隔离负载工具的`--bulk-extra`可临时增加第二个批量API进程，要求`--priority-api`且不得与批量故障注入同时使用。工具单独核算实际多出的10个连接、等待额外副本就绪并将网关指向两个批量后端；跳过单后端停机探测。**这不是生产Compose副本开关**：生产Settings/网关/健康与故障恢复尚未支持批量副本，不能直接对`bulk-transfer-api`执行扩容。与加长批量队列的200 Worker对照见[数据库容量验收](database-capacity-acceptance.md)。

API 隔离本身不替代断联执行恢复。新恢复协议需 API 与节点显式启用、持久 outbox 和能力协商；未启用的旧执行仍沿用原租约行为。当前恢复发布状态、真实协议矩阵与灰度边界见 [恢复验收](validation/disconnected-execution-20261124/recovery-acceptance-20261126.md)。

## 分开配置请求准入和数据库容量

新增可选参数仅用于四角色模式；不填写时沿用原额度和等待，单/双API不受影响。所有角色必须拿到相同JSON配置：

| 参数 | 含义 | 缺省 |
|---|---|---|
| `PRIORITY_API_ADMISSION_LIMITS` | 各类同时处理的HTTP请求数 | 对应数据库池大小 |
| `PRIORITY_API_QUEUE_LIMITS` | 各类入口等待人数，0表示不排队 | HTTP准入数的4倍 |
| `PRIORITY_API_QUEUE_WAITS` | 入口最长排队秒数，0表示立即准入或拒绝 | DB_POOL_TIMEOUT_SECONDS |
| `PRIORITY_API_POOL_WAITS` | 各类实际取数据库连接的等待秒数 | DB_POOL_TIMEOUT_SECONDS |
| `PRIORITY_API_REQUEST_BUDGETS` | 从入口开始计算的SQL总预算 | 短类4秒，user_long30秒 |

JSON可仅覆盖需要调整的类别，未知类别或非法值拒绝启动；scheduler只有数据库池，没有HTTP准入。请求和队列额度分别要求正整数/非负整数；等待必须有限。显式设置的排队等待与连接等待之和必须小于该类总预算；Worker类别预算严格小于5秒，交互类别最多60秒。数据库锁等待仍受独立参数和剩余总预算共同限制。总预算不是任意Python函数的强制终止时间。

例如页面读取数据库仍10个连接，可以配置HTTP准入32、队列160、排队2秒、取连接1秒、SQL总预算12秒。并不保证32个SQL并行：同类最多10个连接，其他请求可能等待。更长数据库等待可能占住同步线程，更多HTTP请求可能增加CPU/内存成本；同进程线程额度至少40或HTTP名额总和加4，以较大者为准。类别间不借用容量，日志仍不能挤占心跳或用户操作名额。

候选文件 `deploy/profiles/team-capacity-candidate.json` 用于百人/百节点专项：只提高HTTP与分类等待，数据库总预算仍80。它不是Compose覆盖文件，也不会自动读取；将各键对应JSON写入上述环境变量后，用原完整Compose命令重新创建API容器才生效。**候选配置不是已经证明的百节点生产容量保证**，实测结果以相关验证报告为准。

节点数不同于Worker数：100台8卡节点可能有800个设备Worker。评估同时记录在线用户/活跃页面刷新、节点/卡数、任务与服务实例数、请求率、连接持有时间、成功响应延迟、503原因及积压是否增长。数据库max_connections是连接硬上限，不是吞吐能力；若要提高PRIORITY_API_POOL_SIZES，还须相应核对DB_CONNECTION_BUDGET、预留和数据库硬件，不能只调单池。

短测工具可加 `--web-users 100 --capacity-config deploy/profiles/team-capacity-candidate.json`。新增用户为隔离库内100个独立管理员身份，模拟每5秒查看共享批次/列表/资源池，页面多个请求并发；原控制场景继续提交/暂停/恢复/重评测。该模型验证多人API流量，不代表100个真实浏览器或100人同时提交；长测仍须另行验收。

排查容量时可在隔离测试环境短时启用 `DB_BUDGET_DIAGNOSTICS=1`。API 日志会按类别记录入口准入等待、每次数据库池获取等待及超时，不包含请求体、令牌或 SQL 参数；负载摘要中的 `admission_wait_ms`、`pool_checkout_wait_ms` 可用于区分 HTTP 排队和数据库取连接排队。诊断会增加日志量，生产长期运行保持关闭；原 `initial_checkout_wait_ms` 只覆盖首次显式取连接。采样与判定口径见 [数据库容量与业务时延验收](database-capacity-acceptance.md)。

交互角色也支持显式多副本：`API_INTERACTION_REPLICAS`默认1，允许1至8，Compose中的api服务按此扩容；`API_DB_PROCESS_COUNT`必须同时设为`2 + API_CONTROL_REPLICAS + API_INTERACTION_REPLICAS`。例如执行控制2副本、交互4副本时总进程8；worker-data与bulk-transfer仍各1，调度器仍唯一。PRIORITY_API_POOL_SIZES中的user_action/user_long/page_read为**每个交互副本**的连接数，总预算必须乘副本数，不能把多副本当成免费容量。健康指纹包含副本配置；角色健康检查不保证每一个副本健康，仍需Compose核对。单进程扩大池仍达不到目标吞吐时，可以在有CPU余量的前提下测试多交互进程。

资源池列表的容量成本：`memberships.summary` 按单次请求批量读取设备账号、目标池和待清理槽位，并以SQL分组统计任务状态；避免每卡额外查询和将全部历史任务状态读入Python。就绪规则、缺失账号/目标池、禁用、待迁移和待清理的含义不变；快照不跨请求缓存，调度/资源修改不使用此展示上下文。回归测试`test_pool_summary_scale`在101个槽位下验证摘要与逐卡旧读取结果一致、查询数不随卡数线性增长。

大批次监控响应使用`MonitorResponse`直接编码原生字典/列表树，只对日期等非原生叶值沿用FastAPI转换，避免对每个任务的每个节点诊断再进行完整Python递归遍历。接口字段、日期格式、权限检查和诊断内容不变；非法非有限数字仍拒绝。此优化不缓存监控快照、不改变调度计算。

## 百人团队候选配置与升级

[2026-11-26专项](validation/team-capacity-20261126/README.md)给出了全部失败对照与通过边界。`deploy/profiles/team-capacity-100.env.example`可按键合并进现有`.env`，保留凭据、存储等原配置；它与同名JSON配置有自动一致性测试。该档交互8副本（每副本操作3、长操作1、读取10），执行控制2×12、数据22、批量10，实际最多168连接；数据库总预算180另预留20，要求PostgreSQL实际max_connections至少200。HTTP页面每副本12、队列160、排队最多3秒、连接等待3秒、SQL总预算12秒。此等待不会把Worker五秒超时一并延长。

不要只改`.env`而不重建容器。先确认没有活跃或待清理执行、备份数据库与原环境；用数据库管理员按既有部署方式将`max_connections`调至200并重启数据库，再使用原完整Compose文件列表`config --quiet`、`up -d --no-build --wait api execution-control-api worker-data-api bulk-transfer-api api-gateway`，镜像须先构建为当前代码。保留原只读共享挂载及纳管设置，随后重载网关DNS（`nginx -s reload`）并逐副本检查健康及角色配置指纹。此配置变更没有数据库迁移；跨版本升级仍需核对迁移头。

回退时恢复原环境和镜像，按原Compose文件重建五个API进程及网关，数据库200连接硬上限可暂时保留；不要降级数据库或删除执行记录。**100 Worker短测通过不等于800 Worker通过**，八卡规模失败数据也保存在报告中，不能用于承诺长期生产容量。

2026-11-26复测决定保持本机合计168连接，不自动调至180：100模拟Worker/100Web用户持续30分钟通过，168/180/延长等待三档对照都没有数据库池超时，首次503主要来自执行/调度锁。扩大池未显示明确时延或失败率收益，且更大池会压缩PostgreSQL其他客户端余量。百人配置的批量入口队列现为192、等待2秒，其他入口和数据库连接数不变；旧30分钟数据来自队列96、等待1秒的历史版本。[新配置90秒复核](validation/heartbeat-cadence-20261126/bulk-queue-100-recheck.json)中100模拟Worker/100Web用户通过、批量入口拒绝0，但不能以短测替代新配置30分钟验收。证据及未验证规模见[容量验收](database-capacity-acceptance.md)；此决定不改变其他部署的配置。


### 分类等待与执行锁

当前百人配置维持168个连接，不继续扩大数据库连接数。执行控制入口/连接等待各1秒；观察入口等待1秒、队列128；批量日志与产物入口等待2秒、队列192，两者连接等待仍为0.5秒。页面仍为入口3秒、连接3秒；Worker SQL预算4秒、HTTP超时5秒不变。只改等待不是无限排队，队列满或预算耗尽仍返回可重试503。60秒候选对照及180秒复验见[等待验证](validation/wait-tuning-20261126/README.md)，批量入口新对照见[容量验收](database-capacity-acceptance.md)，不能据此承诺长期生产容量。

心跳启动确认和原执行恢复不再无条件与调度器争用全局锁；具体保留/取消范围、503原因及锁顺序见[执行锁范围](execution-lock-scope.md)。调度分配与资源释放仍受全局锁保护。本次无数据库迁移，不要求更新Worker。

数据库池对照与业务时延验收目标见[数据库容量验收](database-capacity-acceptance.md)。该文区分配置硬上限、性能目标与尚缺的测量，不代表已验证SLA。
