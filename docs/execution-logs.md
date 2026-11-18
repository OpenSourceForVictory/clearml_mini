# 执行日志

日志 v2 是滚动窗口，不是完整业务日志归档。平台每个执行最多保留 256 KiB 正文或 256 个块；新内容到达后淘汰旧块。旧 Worker 仍使用原接口，保留最前 256 KiB。Worker 0.21.0 通过 bounded_logs_v2 能力协商启用；宿主机和注入当前 Runner 的容器支持 v2，旧镜像内置 Runner 和旧执行 journal 保持旧模式。此变更仍待发布，不能据此认为已纳管节点已自动升级。

## API

Worker POST `/api/v1/worker/executions/{execution_id}/logs-v2`：`sequence` 从 0 单调增长，`content` UTF-8 最多 16 KiB，`stream` 为 stdout/stderr/system，`gap` 表示采集缺口。空内容仅在 gap=true 时允许；拒绝 NUL。请求必须使用拥有该执行槽位的 Worker 凭据，且执行租约有效。

同序号同内容重试不重复写入；不同内容冲突返回 409。已淘汰且无法校验摘要的过旧序号返回 accepted=false、resync=true、next_sequence，发送方不能将其当作当前正文被接受。每个发送方仅有一个持久 pending 块。新内容在执行关闭后被拒绝。

任务日志 GET 保留原地址与 after/limit，增加 version、first_sequence、gap、truncated、complete；chunks 增加 stream/gap。跨窗口游标会显式提示缺口。旧表读取兼容，不改写历史日志。

## 存储、并发和升级

迁移 y518b90 新增 run_log_states/run_log_chunks，保留 run_logs；先备份和升级 API 数据库，再部署 API，节点版本尚未启用前不产生 v2 记录。新表有执行外键，批次删除按子表先于 Run 清理。日志上传以 Run 行锁串行化同执行追加，无全局调度锁；其他控制路径仍按全局锁→Run 的顺序取锁。日志不更新旧 Run.log_bytes。

降级迁移会删除 v2 日志表及数据，必须先停止新 v2 执行、清理在途执行并备份；一般回退代码保留新增表。不能将回退描述为保留 v2 历史读取能力。

测试：test_execution_logs_v2.py、test_execution_logs_postgres.py；后者只在一次性 PostgreSQL 数据库中执行，包含迁移保留旧日志、上传幂等、结束/删除竞态。测试通过数量不替代项目覆盖率门槛。

## 节点采集与界面

节点环形文件最多 256 个记录槽，每块最多 2048 UTF-8 字节；即使字符被 JSON 转义，诊断日志仍控制在 4 MiB 内。stdout/stderr 都在运行期间采集。Docker 使用有界尾部接续，断线或重启标记缺口；不承诺完整历史或跨流严格时间排序。

另外保留最多 7 KiB 的 stderr 尾部快照，避免 Docker 延迟交付的大量 stdout 将最终错误挤出共同窗口。退出补传可能重复展示这段 stderr，并标记缺口；它不是完整错误日志归档。

上传独立于执行心跳，每请求最多 16 KiB，每 2 秒至多一次，失败退避最多 30 秒；未确认块先写入独立 `.logs` journal，避免被执行恢复的 `*.json` 扫描当作任务。退出前分别保留 stdout/stderr 的最新尾部，补传最多 5 秒，期间继续心跳。未补传完整或因启动取消、数据拒绝等异常提前结束时，完成请求标记日志缺口，不让日志故障无限阻塞结果和清理。

最终协议结果与回合事件独立保存，回合 journal 有单独 32 MiB 上限，超过上限明确失败，不静默丢弃事件。容器专用 `.mini-eval-protocol` 目录不作为业务产物上传。业务参数、登录环境继承、设备可见性和成功判定不变；常驻服务退出 0 仍不是服务持续运行成功。

客户端和服务实例面板展开后增量读取，关闭后停止轮询；终态日志读尽后停止自动刷新，可手动刷新。标注 stderr/system 来源；旧日志淘汰、采集断线或结束补传不完整时显示缺口提示。界面保留最新 256 KiB，不提供完整归档。

验证范围、失败记录和性能取舍见[有界日志验证](validation/bounded-execution-logs/README.md)。
