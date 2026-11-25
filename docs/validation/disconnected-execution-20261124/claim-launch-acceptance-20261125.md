# 幂等领取与一次启动边界验收（2026-11-25）

4.8 的生产代码早于本轮已接入 `Worker.execute`：领取请求 ID 和首次任务快照先持久化，再移交执行 journal；启动前按原分配代次、boot 和一次性 launch_id 向平台确认，节点在实际启动副作用前持久化 attempted。响应丢失只能查询原收据与原运行实体。宿主机按 systemd InvocationID，Docker 按完整容器 ID 及启动标签核验，不凭同名 unit、容器或 PID 重试。平台取消/超时先于未使用的启动许可；首发窗口及业务时限不会因重放刷新。

隔离 PostgreSQL 与真实 systemd/Docker 短程序专项：领取收据、启动确认、Worker 持久 journal、实际后端和本地监督共 187 通过、无跳过；同时另有 `test_recovery_execution_real.py` 16 个完整 Worker.execute 宿主机/Docker 场景通过。相同 187 项专项独立覆盖率：`recovery_claims.py`、`recovery_launch.py`、`worker/recovery_claim.py`、`worker/recovery_launch.py` 行/分支均 100%；`worker/recovery_poll.py` 行 93.88%、分支 90.00%。这不是全项目覆盖率。覆盖提交响应丢失、attempt 前/后崩溃窗、身份写入失败、两位监督者互斥、同名实体被新 InvocationID/完整 ID 替换、新 boot 恢复拒绝、业务时限保留。机器重启由 boot ID 改变模拟，未重启业务真机；同名实例替代检验的是防 PID 复用的实际身份机制，不声称强制复现内核 PID 回绕。没有完整恢复协议的生产灰度或模型评测。

该项只验收领取与启动边界。4.3/4.4/4.7/4.9/4.10 的数据、删除与完整链路，以及 5.x 长测/发布验收各自仍待完成。生产 `disconnected_execution_v1` 能力尚未广告，恢复开关保持关闭。
