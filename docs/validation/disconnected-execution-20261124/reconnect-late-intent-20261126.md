# 重连后迟到完成与平台控制意图

触发场景：宿主机或 Docker 业务短程序在 API 断联期间完成并留下原执行结果；在节点重连之前，平台已经提交取消或业务超时。恢复上报不得把平台状态改回成功，也不得再次启动该程序。

在 `backend/tests/test_recovery_execution_real.py` 增加 `offline_cancel_after_exit`、`offline_timeout_after_exit`，分别在真实隔离 systemd 与 Docker 后端执行。测试等业务退出、超过旧的一秒租约且 Worker 仍保留原执行后，先把平台 Run 更新为取消请求或超时，再恢复 API。四项定向测试通过：最终 Run 分别为 `CANCELLED`、`TIMED_OUT`，均完成物理清理和结果同步收尾，`create` 仅调用一次。使用的是短 Python 程序、隔离 SQLite API 和注入的网络故障，不是模型评测或实际跨机断网。

一次性 PostgreSQL 16.8 容器运行 `backend/tests/test_recovery_control.py`、`test_recovery_records.py`、`test_recovery_results.py`、`test_recovery_finish.py`、`test_recovery_claim_integration.py`，202 项通过、无条件跳过。此组覆盖原执行/代次、同序号异摘要、旧凭据与新凭据、取消优先、重复报告、旧领取隔离、迁移和锁竞争；其余恢复执行矩阵、真实跨机和灰度启用仍缺验收。因此 OpenSpec 4.7 仍不勾选，生产恢复能力仍保持关闭。
