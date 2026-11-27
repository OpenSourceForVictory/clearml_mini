# 批次服务超时后的停止与恢复验证

现场描述：在“查看批次 → 服务端与关联”中，服务显示“执行心跳租约过期”，停止或恢复不能完成。未取得该部署的 HTTP 响应正文和节点日志，不能把现场所有 409 都断定为同一个原因。

代码复核发现两条独立路径：`scheduling.reconcile` 将过期执行标为 `TIMED_OUT`、保留 `cleanup_confirmed=false` 并隔离设备；批次停止接口仍调用 `cancel_job`，后者在任务已终态时抛出 409 `terminal job`。恢复接口因旧执行尚未清理，返回 409 `wait for current service cleanup before restarting`；旧版本号仍返回 409 `service changed; refresh`。后两者是资源与并发保护，不能简单取消。

修正：停止接口在任务已经结束或停止期间刚完成时，仍写入 `desired_running=false`，只忽略 `terminal job` 这一特定冲突；其他 409 保留。页面明确显示等待 Worker 清理，并在清理未确认时禁用恢复。节点 Worker 回连、确认原执行已停并上报清理后，才允许恢复服务或继续节点升级。没有自动清除隔离状态、修改任务终态或重派原执行。

验证：`backend/tests/test_orchestrations.py` 覆盖已结束任务停止、完成竞态、其他 409、版本冲突，以及超时后“停止成功—恢复被阻止—清理确认后可恢复”；连同服务与升级回归 45 项通过，新增超时专项另通过。前端 `orchestration-status.spec.ts` 4 项通过，`npm run build` 通过。上述使用隔离测试数据库和模拟 HTTP；未在用户所述的其他部署机器实测 Worker 回连和业务服务恢复，也未运行全项目覆盖率。
