# 恢复服务、重新评测与数据库繁忙

恢复服务、单任务重新评测和批次重试失败项均走控制通道；双进程部署时与日志的普通 API 进程、连接池隔离。请求仍共享 PostgreSQL 实例及资源一致性锁，不能保证任意负载下都不失败。

## 操作结果

服务控制成功表示请求已经入库，不表示业务已经启动或就绪。界面随后单独读取状态；若提示“操作已接受，状态刷新失败”，稍后刷新核对，不必重复恢复。单任务重评也区分请求成功和详情刷新失败。批次重评保留选中的旧执行 ID，重复提交原选择不会创建重复尝试。

两种服务控制接口（`admin/services/{id}/control`、`orchestrations/{batch}/services/{id}/control`）现返回：

```json
{"accepted": true, "id": "实例ID", "revision": 2, "current_job_id": "任务ID", "action": "start"}
```

这是简短确认，不再返回完整实例或编排详情。外部调用者需通过对应的现有 GET 接口查询详情。提交前保存确认字段，提交成功后不再执行详情查询；提交失败不会返回成功。网络丢失仍存在结果未知的情况，先核对状态和版本再操作。

## 繁忙原因与调度锁

- `request_budget`：请求总预算耗尽，包含线程等待、业务计算和查询；不是单纯连接池数量不足。
- `connection_pool`：等待连接超时。
- `lock_timeout`：数据库锁等待超时；恢复与重评仍需要调度锁。
- 调度器尝试全局锁失败会跳过该轮，默认约 2 秒后再试，不会因此直接将任务判失败。持续无法取得锁需要检查持锁事务，不应手工改状态或删除一致性锁。

最近 `9e59013` 引入默认 0.5 秒锁等待和 4 秒 HTTP 请求预算，可能将原本长时间等待变为明确的繁忙响应；双进程并未隔离数据库锁。此次缩小批量重评查询至选中任务（最多 100 项），保留权限、完整尝试历史、清理、积压额度及调度锁。未修改调度算法与锁等待预算，也未声称已消除所有持锁延迟。批次监控 GET 的优化单独跟踪。

管理员可在故障数据库执行下面的只读查询，确认持有或等待平台调度锁的事务（不输出 SQL 参数或凭据）：

```sql
SELECT a.pid, a.application_name, a.state,
       now() - a.xact_start AS transaction_age,
       l.granted, a.wait_event_type, a.wait_event,
       pg_blocking_pids(a.pid) AS blocked_by
FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
WHERE l.locktype = 'advisory' AND l.classid = 0
  AND l.objid = 82510401 AND l.objsubid = 1
ORDER BY l.granted DESC, a.xact_start;
```

`granted=true` 是持有者；持有者同时等待其他行锁时，也会阻止调度。采样未查到不证明从未竞争，需在错误发生时核对。不要自动终止持锁事务。

## 部署

本功能无新增数据库迁移，无需更新 Worker。必须同时更新普通 API、控制 API 镜像和网关配置；仅替换一个 Python 文件或重启旧镜像不足以更新路由。

沿用 [双 API 部署指南](split-api-startup.md) 的 `dc` 函数及全部原有 Compose 参数。在维护窗口按原流程更新 API；网关使用 `dc up -d --no-deps --force-recreate api-gateway` 重新读取 nginx 配置。保持原平台端口不变。回滚时 API 与网关配置一同回滚。

验证应包含恢复服务、单任务重评、批次重评及心跳；独立数据库的锁竞争/连接池耗尽测试不等于现场负载验收。
