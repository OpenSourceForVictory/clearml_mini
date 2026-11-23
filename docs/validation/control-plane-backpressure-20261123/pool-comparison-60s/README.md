# 200 Worker 连接预算对照（60 秒）

2026-11-23 当前开发工作树的专项诊断；未部署，不是完整容量验收。单 API 进程，隔离 PostgreSQL，通过真实 HTTP 请求。三组顺序执行，各 200 个模拟 Worker、60 秒；观察/结果/调度池固定 2/2/1。未启用 Agent、实际调度分配、Web 或真实业务进程。其他在线进程仍可能使用同一主机，且每组仅一次、含随机抖动，不能作严格性能因果归因。

| 普通池 | 控制池 | 总连接上限 | 最大续租间隔 | 心跳入口拒绝 | 日志入口拒绝 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 10 | 5 | 20 | 18.64 秒 | 2823 | 12 | 未通过 |
| 5 | 10 | 20 | 9.77 秒 | 1026 | 2864 | 短测通过，余量很小 |
| 10 | 10 | 25 | 12.13 秒 | 1861 | 1089 | 未通过 |

通过条件仍为最大续租间隔 <10 秒、无过期租约/遥测、各类请求 P99 <5 秒且仅返回 200/503，未调整门槛。三组实际过期租约/遥测均为 0；拒绝分类见同目录 JSON，并未观察到 connection_pool 分类。控制池配置同时影响入口准入与可用连接，因此本实验不是纯粹只改变 PostgreSQL 连接数的实验。

结论：增加控制额度有帮助，但增大总预算并未保证通过；普通流量放开时控制流量仍共享 API CPU/线程调度及数据库资源。未采集足够的 CPU/IO/等待采样，不能认定是哪项资源饱和。保留默认配置，不宣称支持稳定 200 Worker。

## 日志负载的真实与模拟部分

每个模拟 Worker 目标约每 2 秒一轮，先成功心跳，再上报健康、日志、产物及领取请求。心跳失败时先退避重试，日志量随成功心跳下降，不能称作独立恒定日志压力。

日志通过真实 `POST /api/v1/worker/executions/{execution_id}/logs-v2`，内容为 1024 个 `x`；服务端真实执行鉴权、Run 行锁、序号校验，并写入 RunLogChunk 和 RunLogState，成功返回后继续使用 next_sequence。不是返回成功的桩接口；但没有启动真实业务程序采集 stdout/stderr，没有模拟日志突发、独立日志持续生产、积压队列排空或断网补传。约 100 KiB/s 只是 200 Worker 每两秒发送一次时的理论提交量，不是实测成功写入速度。

后续完整混合负载应补持续日志生产、失败后积压/重试、队列年龄及最终交付校验，同时测 Agent、多卡、Web、调度与完成清理；本次未勾选 OpenSpec 长时混合验收任务。

## 复现

使用 `backend/tests/control_plane_load.py`，仅配置隔离测试 PostgreSQL 管理地址 `LOG_TEST_DATABASE_URL`，不要指向生产数据库。每组执行下述命令，分别将 ordinary/control/output 改为 10/5/default、5/10/rebalanced、10/10/expanded；工具创建并删除独立数据库。

```bash
DB_OBSERVATION_POOL_SIZE=2 DB_RESULT_POOL_SIZE=2 DB_SCHEDULER_POOL_SIZE=1 \
.venv/bin/python -m backend.tests.control_plane_load \
  --grades 200 --seconds 60 --ordinary-pool 10 --control-pool 5 \
  --output .runtime/pool-comparison-default
```

本次不执行全量测试，不修改生产配置。JSON 为实际测试计数，不含认证凭据；原始 API 诊断日志保留本地对应 .runtime 目录。
