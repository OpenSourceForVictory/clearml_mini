# 数据库迁移与 Compose 升级指南

适用于使用 `deploy/compose.yml` 部署的 Mini Eval PostgreSQL 平台。以下命令在**平台服务器的项目根目录**用 Bash 执行，不在 GPU/NPU 节点执行。

**已有平台升级必须按顺序：取得目标代码或镜像 → 构建或拉取目标迁移镜像 → 排空任务、停止 API 并备份 → 执行迁移 → 启动并验收。** 不要只在旧 API 容器中检查版本，就认定本次升级“不需要迁移”。

`current` 是所连接数据库已记录的版本；`heads` 是运行命令的镜像所包含的最新迁移版本。旧镜像可能让两者都显示 `x407a89`，这只能证明数据库符合旧镜像，不能证明符合目标新版本。历史日志存储优化版本的目标是 `y518b90`；仅更新 Worker 0.21.1 的失败恢复修复则没有额外迁移。

批次排队优先级新增迁移 **aa741b2**（前驱 c952f34），为 `evaluation_batches` 增加默认值为 0 的 priority 列及 0–9 范围约束。升级会保留历史批次并统一设为默认优先级，不需要更新 Worker；按下文的 Compose 升级步骤执行 `alembic upgrade head`，并以目标镜像 `heads` 和数据库 `current` 一致为准。

历史 API 共享存储直读新增迁移 **a730d12**（前驱 z629c01），创建 shared_artifact_roots 目录元数据表；不搬迁文件或修改旧 RunArtifact 正文。该功能单独发布时 heads/current 为 a730d12，下面 y518b90 的示例是历史版本说明，不能作为本次升级终点。共享挂载 overlay 与配置见[共享产物部署指南](shared-storage-artifacts.md)。

历史日志预算配置新增迁移 **b841e23**（前驱 a730d12），仅新增 `execution_log_policies`，保存每次执行领取时已确认的日志预算。部署此版本前必须执行迁移，并确认目标镜像 `heads` 与数据库 `current` 均为该版本对应的 b841e23。历史执行、日志和产物不回填、不搬迁；旧 Worker 继续原行为。降级会删除配置快照，优先保留新增表回退应用。本文描述目标代码，不代表其他部署机器已升级。

**历史自定义结果浏览目录版本的目标迁移版本是 `c952f34`（前驱 `b841e23`）。** 它增加自定义结果浏览目录配置及执行路径元数据。2026-11-20 批次排队优先级发布在其后增加迁移 `aa741b2`（前驱 `c952f34`）；本机本次部署已将数据库升级到 `aa741b2`。其他部署按目标镜像的 `heads` 与数据库 `current` 核对，不能把历史版本号当作当前终点。

当前执行时间功能新增迁移 **ab852c3**（前驱 aa741b2），仅增加 `job_events(run_id,new_status,created_at)` 索引，不新增时间表或回填历史数据。PostgreSQL 并发建索引，失败重试时会移除未完成的无效索引再建；不要把迁移版本手动改为 head。执行 `upgrade head` 后核对目标镜像 heads/current 均为 ab852c3。旧 API 可继续写入；降级只移除新增索引，不删除事件及已有 timing JSON。2026-11-23 本机已迁移至 ab852c3，见[上线记录](validation/execution-duration-20261123/README.md)；其他部署仍须自行核对 heads/current。

模板说明新增迁移 **ac963d4**（前驱 ab852c3），为 client_templates 增加默认空字符串的 description 列；服务端沿用 definition JSON，旧定义缺字段按空说明读取。先迁移后更新 API/前端；Worker/Runner 无需更新。历史任务快照不回填，降级移除列会丢失客户端模板说明，优先保留列回退应用。此项为待部署变更，不代表现场已经升级。

## 执行前核对（另一台部署机器）

先进入那台机器存放本项目的目录，确认存在 `deploy/compose.yml`。不需要使用开发机器目录或连接 29 节点。

```bash
pwd
test -f deploy/compose.yml
docker compose version
```

准备以下信息再执行后续命令：

1. 原部署使用的环境文件路径：示例为 `.env`，实际不同则替换 `--env-file`。
2. 原 Compose 项目名：标准值为 `mini-eval`，实际不同则替换 `-p`，不能为了照抄文档改变原项目名。
3. 原部署使用的全部 `-f` 文件及顺序，包括 LAN 和固定镜像版本覆盖文件。
4. 目标代码或镜像已到这台机器。源码构建需要 Docker 能访问依赖源；离线环境应先导入准备好的完整目标镜像。
5. 备份位置有足够空间，Docker 权限及支持 `up --wait` 的 Compose 插件可用。

本文的数据库服务名 `db`、数据库名和用户名 `eval` 来自项目默认 Compose。自定义过这些值的部署必须同步调整命令。文档不会自动识别、迁移或重启任何机器；只有你在目标部署机器执行命令才会生效。

## 1. Compose 是否会自动迁移

会。当前配置的依赖顺序是：

```text
db 健康 → migrate 成功退出 → bootstrap 成功退出 → api
                                        onboarding 健康 → api
```

`migrate` 使用 API 镜像，执行 `alembic -c backend/alembic.ini upgrade head`。`bootstrap` 初始化缺失的管理员，不会通过修改初始密码重置已有账户。`migrate` 和 `bootstrap` 显示 `Exited (0)` 是正常完成；API、数据库和 onboarding 才是常驻服务。

- `up -d --build` 可以构建并启动包括迁移在内的服务。
- `restart api` 不构建新镜像、不执行数据库迁移，也不加载修改后的 Compose 环境配置。
- `up --no-deps api` 跳过迁移依赖，不能用作尚未迁移数据库的完整升级命令。
- 迁移失败会阻止依赖它的新 API 启动，但**不会保证停止已有的旧 API**。已有部署应按下文安排维护窗口。

## 2. 沿用原部署配置

项目名、环境文件、全部 Compose 文件及顺序必须与原部署一致。更换项目名可能连接到新卷，让平台看起来像“数据丢失”。环境文件内的数据库密码也应保持原值；修改环境变量不会自动修改已有 PostgreSQL 账户密码。

标准部署示例：

```bash
DC=(docker compose --env-file .env -p mini-eval -f deploy/compose.yml)
"${DC[@]}" ps -a
```

若原来使用 LAN overlay，改用下列定义；只有实际使用该文件且环境文件已配置 `WORKER_BIND_HOST` 时才加上：

```bash
DC=(docker compose --env-file .env -p mini-eval \
  -f deploy/compose.yml -f deploy/lan-compose.yml)
```

若原部署还带镜像版本覆盖文件，在数组末尾继续加原来的 `-f /实际路径/镜像配置.yml`。这些例子是选择关系，不要依次执行来覆盖自己的配置。不要输出完整 `docker compose config` 或环境文件后贴到工单，其中可能包含凭据。

**API、migrate、bootstrap 必须使用同一个目标版本的 API 镜像。** 只覆盖 api 镜像而让 migrate 使用旧镜像，会出现应用已更新但表结构未更新的问题。

## 3. 已部署平台的升级步骤

每一步成功后再执行下一步。命令失败时停止，不要继续启动新 API。

### 准备目标镜像

先取得已验证的目标代码版本。源码构建部署使用：

```bash
"${DC[@]}" build api migrate bootstrap onboarding
```

如果使用已发布的固定版本镜像，则先在原镜像覆盖文件中同步设置 api/migrate/bootstrap 的目标镜像，再执行 `pull`，不要执行上述构建命令覆盖固定镜像：

```bash
"${DC[@]}" pull api migrate bootstrap onboarding
```

不要在此次应用升级中顺便更换 PostgreSQL 主版本；Alembic 表结构迁移不负责 PostgreSQL 大版本升级。

构建或拉取完成后，先确认**目标迁移镜像**包含预期版本（此命令只读取镜像中的迁移文件）：

```bash
"${DC[@]}" run --rm --no-deps migrate alembic -c backend/alembic.ini heads
```

升级本轮日志存储优化时，预期为 `y518b90`（或包含它的后续版本）。若仍为 `x407a89`，先停止升级流程，检查目标代码是否已更新、镜像是否重新构建或拉取、原 Compose 覆盖文件是否仍指定旧镜像。源码部署可检查 `backend/alembic/versions/y518b90_bounded_logs.py` 是否存在，但文件存在不代表镜像已更新。解决后再进入维护与备份步骤。

### 排空并停止 API

在界面暂停新任务提交，等待客户端、托管服务和清理任务结束。停止 API 会中断 Worker 心跳续租；不要在活动业务仍运行时直接停服务。

```bash
"${DC[@]}" stop api
```

数据库保持运行。需要临时恢复旧 API 时，应先判断迁移是否已执行以及旧版本是否兼容，不能直接假设回退镜像即可恢复。

### 备份并检查

记录旧 API 镜像版本、当前环境文件与 Compose 文件位置，另行安全备份部署配置。备份目录不得提交 Git。

```bash
umask 077
mkdir -p .runtime/db-backups
DB_BACKUP=".runtime/db-backups/eval-$(date +%Y%m%d-%H%M%S).dump"
"${DC[@]}" exec -T db pg_dump -U eval -d eval -Fc > "$DB_BACKUP"
```

确认 `pg_dump` 成功退出后执行：

```bash
test -s "$DB_BACKUP"
"${DC[@]}" exec -T db pg_restore --list < "$DB_BACKUP" > /dev/null
sha256sum "$DB_BACKUP" > "$DB_BACKUP.sha256"
```

上述检查只验证文件非空和归档目录可读，**不等于恢复演练成功**。重要升级前应恢复到独立临时数据库并验证迁移和历史数据，不能用生产数据库试恢复。备份含业务数据与认证相关记录，应限制访问并复制到受保护的外部备份位置。

### 显式执行迁移

先查看目标镜像识别的当前版本和目标版本：

```bash
"${DC[@]}" run --rm --no-deps migrate alembic -c backend/alembic.ini current
"${DC[@]}" run --rm --no-deps migrate alembic -c backend/alembic.ini heads
```

执行迁移：

| current | 已确认的目标镜像 heads | 判断 |
| --- | --- | --- |
| `b841e23` 或更旧版本 | `c952f34` | 需要执行下方迁移命令，Alembic 顺序应用中间迁移。 |
| `c952f34` | `c952f34` | 本轮迁移已完成，仍需完成启动与验收。 |
| `x407a89` | `x407a89` | 如果目标是本轮日志存储优化，镜像不正确；返回准备目标镜像，不能判断为无需升级。 |

上表使用本轮自定义目录版本 c952f34；若目标镜像包含更新迁移，以该镜像的 heads 为准，不使用旧文档编号覆盖它。

```bash
"${DC[@]}" run --rm --no-deps migrate
```

该命令继承 Compose 中的 `upgrade head`，只有退出码 0 才继续。`--no-deps` 在这里用于避免隐式操作其他服务，前提是现有 db 已运行且健康；它不跳过当前命令的迁移操作。

### 启动与验收

```bash
"${DC[@]}" up -d --wait
"${DC[@]}" ps -a
"${DC[@]}" logs --tail=100 migrate bootstrap
"${DC[@]}" exec -T api alembic -c backend/alembic.ini current
"${DC[@]}" exec -T api alembic -c backend/alembic.ini heads
"${DC[@]}" exec -T api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status)"
```

`up` 可能再次运行迁移服务；已经达到目标版本时 `upgrade head` 不会重复执行已记录的迁移。确认 current 与目标镜像的 head 一致、API 健康、页面可登录、历史任务可查看、各节点心跳恢复，再开放任务提交。用一次小任务核对调度、执行及日志，不能仅以容器 Running 判断业务可用。

这里使用 `run --rm` 执行的迁移日志显示在终端；容器退出后已删除，后面的 `logs migrate` 只显示 Compose 常规 migrate 容器日志，不会补回临时迁移容器输出。失败时保留终端报错，并检查是否包含敏感信息后再分享。

## 4. 首次安装的简化方式

首次安装没有旧任务和旧数据库需要排空，在准备好环境文件、镜像与正确的 DC 定义后，可以直接：

```bash
"${DC[@]}" up -d --build --wait
"${DC[@]}" ps -a
"${DC[@]}" logs --tail=100 migrate bootstrap
```

固定版本镜像部署改为先 `pull`，再 `up -d --wait`，不加 `--build`。

## 5. 失败时怎么处理

| 现象 | 检查与处理 |
| --- | --- |
| 数据库认证失败或连接失败 | 核对原环境文件、项目名、数据库健康状态；不要修改或输出完整 DATABASE_URL 来试错。 |
| `Can't locate revision` | 通常是目标镜像缺少数据库已记录的迁移；核对镜像版本，不要用 `stamp head` 绕过。 |
| 新表不存在 | 核对 api/migrate/bootstrap 是否同版本，迁移是否成功，以及是否误用了 `--no-deps api` 或另一项目的数据库。 |
| 迁移超时、DDL 等待锁 | 检查活动请求和数据库事务；先定位锁持有者，不要盲目结束所有连接。 |
| 迁移成功但 API 不健康 | 查看 api/bootstrap/onboarding 日志，核对环境和挂载；不要重复降级数据库尝试修复应用配置。 |
| 需要回退 | 根据迁移是否成功、旧应用兼容性及备份恢复演练确定方案；生产降级可能有保护条件或删除数据，不能直接 `downgrade base`。 |

不要执行 `docker compose down -v`、删除 PostgreSQL 卷或手动删除 Alembic 版本记录来修复迁移问题。不要将 migration 测试脚本中的破坏性降级命令指向生产数据库。需要备份恢复时，参照 [发布与恢复手册](fleet-release.md)，先在隔离数据库验证。

## 6. 本轮更新与节点升级的关系

- 日志存储优化：迁移 `y518b90`，从 `x407a89` 升级，新增 `run_log_states`、`run_log_chunks`，保留旧日志。后续若已有更新迁移，以目标镜像的 `heads` 为准，不把该编号永远当作最新版。
- Worker 0.21.1 的首次启动失败保存与恢复：不新增数据库表，无额外迁移。
- Compose 更新的是平台服务，不会自动把已纳管节点的 Worker/Agent/Runner 更新到新版本。控制端迁移和健康检查通过后，按 [节点升级说明](worker-upgrades.md) 对需要更新的节点排空并升级。

实现依据：[Compose 配置](../deploy/compose.yml)、[迁移脚本目录](../backend/alembic/versions/)。真实迁移补测与验证边界见 [2026-11-19 验证记录](validation/host-recovery-full-20261119/README.md)。

## 开发中：分作用域日志迁移 z629c01

`z629c01` 接在 `y518b90` 后，新增 execution_log_catalog、execution_log_reads 和 execution_log_read_subscribers。保留旧日志表与数据；目录展示本身不需要迁移，日志索引/按需请求接口需要。新表尚在开发验证中，不应仅根据本节对生产执行迁移。降级会删除新增索引及临时读取数据，默认回滚应保留表。实际发布时仍按前文核对目标镜像 heads、排空备份和恢复演练，不使用 stamp。


## 自定义结果浏览目录版本 c952f34

本轮目标 head 为 **c952f34**，前驱 **b841e23**。新增 client_templates.result_directory 可空字段及 execution_browse_roots；原模板空值保持原行为，旧执行不回填，不读取或搬迁产物文件。

按本文的原部署 Compose 数组构建同版本镜像、排空、备份、恢复演练后运行 `"${DC[@]}" run --rm --no-deps migrate`。核对目标镜像 heads 和数据库 current 均为 c952f34，再启动新 API。无需为了这个 API 字段重装 Worker/Runner。仅更新代码、不重建镜像不能完成升级；不要使用 stamp 跳过迁移。回退应用优先保留新增表；downgrade b841e23 将删除自定义目录元数据和模板字段，但不会删除共享文件。


## Agent 观察状态分表（ad074e5）

此迁移前置版本为 `ac963d4`，新增 `node_agent_observations` 保存 Agent 最新序号、采样及应用回执；期望配置仍在 Worker。不是历史采样逐条落库。升级时先按本文备份并停止所有旧 API 写入，再执行迁移、部署同一版本 API，不能混跑新旧 API。Worker 上报正文不因该分表改变。

2026-11-23 本机平台先用生产备份在隔离 PostgreSQL 验证恢复和升级，再停止旧 API、完成最终备份并迁移到 `ad074e5`；升级后 13 个卡位回连，随后启用双进程。其他部署仍须按自己的目标镜像 `heads` 与数据库 `current` 核对，不能据此认为已完成迁移。

升降级回归使用一次性 PostgreSQL，覆盖历史值回填、空库、当前会话值合并降级、旧会话不覆盖新配置及迁移失败事务回滚。升级失败不能手动篡改 alembic_version；应保留错误，修复原因后重试。降级必须暂停 API 写入，把当前会话最新观察合并回 Worker JSON 后再移除表，不能直接 DROP TABLE。

上述迁移验收不等于整个过载方案可发布；控制通道和剩余锁竞争的范围见 [控制面通道审计](validation/control-plane-backpressure-20261123/channel-audit.md)。尚未实现的断联继续执行/终态补传可能有独立 schema 变更，不能将本迁移视为那些能力已就绪。

## 并发索引迁移的降级回滚（2026-11-24 修正）

`ab852c3` 升级仍使用 PostgreSQL `CREATE INDEX CONCURRENTLY`，不改变在线建索引方式。降级改为事务内 `DROP INDEX IF EXISTS`，应按前文在排空、暂停写入的维护窗口执行：如果更早版本的端点或数据保留保护规则拒绝降级，索引、数据库版本和受保护数据将一起回滚。

旧实现使用并发删除索引，会在后续保护规则拒绝前提交删除；因此可能出现 `current` 仍含 ab852c3，但索引实际缺失、再次 `upgrade head` 也不重建的状态。遇到这种情况先保留失败日志，核对 `alembic current`、`alembic heads`、`alembic check` 及 `pg_index` 中 `ix_job_events_run_status_created` 的存在和有效性；不要用 `stamp` 掩盖差异。应在隔离恢复库验证修复方案，再按维护流程处理现场。

此次修正不新增迁移版本，不要求已经正常升级的数据库再次降级；需更新执行迁移命令的代码/镜像。真实 PostgreSQL 专项验证拒绝降级后版本不变、索引仍有效、端点和保留数据不丢失，并验证重新升级及 schema check。
