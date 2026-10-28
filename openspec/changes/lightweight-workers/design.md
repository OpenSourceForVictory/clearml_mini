# Design

## Context

现有项目已具备用户认证、任务入库、PostgreSQL 迁移、mock Runner 与控制节点 Compose。ClearML Server 官方镜像仅提供 AMD64；当前宿主机为 ARM64，且用户希望减少组件和部署负担。

## Decisions

1. **PostgreSQL 作为唯一持久化队列。** API 保存 Job/Run，单领导者调度器按用户轮转和资源槽位把任务分配给 Worker。Worker 通过 HTTPS API 拉取，不需要 Redis、MongoDB、Elasticsearch 或 ClearML Server。
2. **逐卡槽位。** 每张物理 GPU 用 UUID 识别。Worker 可在单卡或多卡机器上按卡启动；CPU mock 使用单独虚拟槽位。心跳只决定新任务准入，运行任务断联需等待宽限期。
3. **租约和执行令牌。** 分配、认领、续租、完成均在数据库事务内进行。每次尝试有稳定 execution ID 和有限租约；失联恢复先对账，避免旧 Worker 与新 Worker 同时提交结果。旧 Worker 重新连接时按令牌确认归属。
4. **固定执行环境。** 控制节点只允许白名单 benchmark、参数和管理员固定的 Runner 镜像。Worker 用 Docker 启动容器并绑定指定 GPU，用户无法传入任意命令、镜像或挂载。日志与指标限量入库；大工件后续接 S3/MinIO。
5. **身份和网络。** Worker 使用独立凭据，凭据只在管理端生成并以哈希保存。API 默认只监听本机；跨机器使用私网或 TLS 反向代理。用户 JWT 不能调用 Worker 端点。

## Migration

保留现有任务和历史记录，新增 Worker 协议与数据库字段后切换调度。迁移必须可回滚；旧的 ClearML 字段在新执行链路验收后再移除。原 `initial-mvp` 中尚未完成的 ClearML 投递任务由本变更替代。按 P0、P1、P2 实施，真实 RoboTwin 验收单列。

## Risks

- 租约到期时原容器可能仍运行：Worker 必须停止续租失败的容器；调度器等待失联宽限期并通过 fencing token 拒绝旧结果。
- 多卡机器可能重复注册同一设备：数据库唯一约束加 GPU UUID 校验。
- Worker Docker 权限较高：仅部署在可信 GPU 机器，使用固定镜像与参数，不接受任意用户代码。
