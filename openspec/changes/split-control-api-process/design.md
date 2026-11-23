# Design

## Current state

`deploy/compose.yml` 只启动一个暴露 `API_BIND_PORT` 的 API。`backend/app/db_budget.py` 将请求分为 ordinary、control、observation、result，入口准入和数据库池独立，但 FastAPI 同步依赖仍共用进程默认线程池。`backend/app/main.py` 默认启动调度线程。原背压设计明确把独立控制 API 进程留给后续变更。

## Deployment

提供显式 Compose overlay，默认 `compose.yml` 不变。Overlay 移除原 API 的宿主机端口映射，新增同镜像 `control-api` 容器和仅网关暴露原 `API_BIND_HOST:API_BIND_PORT`。两个 API 容器均在内部监听 8000；网关按准确路径转发控制 POST，其余流量转发普通 API。网关保留原 Host、客户端来源和转发协议，不在日志中写凭据或正文。网关 `/healthz` 应同时检查两个 API 的存活，控制 API 不健康时控制请求必须失败，不回退到普通进程。

控制 API 使用 `SCHEDULER_ENABLED=0`；普通 API 保持原调度线程。两者启用数据库背压，`API_DB_PROCESS_COUNT=2`；按两个进程的全部五类池校验连接预算，并保留 PostgreSQL 管理连接余量。两个进程虽各有完整路由表，但请求入口根据自身角色拒绝误路由，防止路由规则漂移或内部直接调用损坏隔离。`/healthz` 为无数据库的例外。

网关路径清单须与 Python `request_category` 的控制分类测试对照，覆盖心跳、服务健康/探测、完成/清理、停止许可、取消和服务控制。Agent 观察、回合结果、日志、产物、领取及 Web 留在普通进程。网关和两个 API 使用独立健康检查；普通 API 的 `readyz` 仍表示数据库可用，不代表控制进程健康。外部地址和 Worker 配置不变。

## Failure and rollout

网关不可用时外部服务不可用；控制 API 不健康时只拒绝控制请求且 Worker 按既有暂时故障策略重试，绝不由普通 API 代办。普通 API 不健康时 Web、日志等不可用，控制进程仍可续租。数据库整体故障、同一行锁和主机 CPU/磁盘竞争仍共享，进程隔离不保证数据库无限容量。

先在隔离数据库做路由与并发验证，再检查目标 Worker 的背压兼容能力、现有执行和数据库预算；发布使用新 overlay 重建，回退时移除 overlay 并恢复单 API。启停调度器不能在切换窗口双开；运行中任务切换仍需按现有发布流程排空或确保兼容，不能清除节点 journal。
