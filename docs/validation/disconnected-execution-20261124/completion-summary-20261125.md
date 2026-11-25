# 完成摘要与结果同步专项，2026-11-25

本项原有代码已将执行完成、物理清理和结果封口拆开：完成只提交原执行身份、退出码、受限指标、错误摘要与 `pending_results`；平台收到完整结果之前以独立 `result_sync` 标记待同步。结果拒收留持久回执，封口时缺回合或摘要不一致记录同步失败，不重开业务执行，也不改已确认的资源释放。旧协议无法使用 `pending_results` 绕过即时结果校验；协商执行仍由能力和开关共同控制，生产尚未启用。

本次补足完成正文的严格边界：未知字段、超长错误、越界退出码及非法指标拒绝，HTTP 返回 400；原始 stdout 不作为完成摘要上送。运行 `test_recovery_finish.py`、`test_recovery_results.py`、`test_result_sync_view.py`、`test_worker_recovery_finish.py`、`test_worker_recovery_results.py`、`test_recovery_claims.py`，使用隔离 PostgreSQL 共 202 passed、2 skipped、0 failed。两条跳过是需 `RECOVERY_RUNTIME_TEST=1` 的真实 host/Docker 收尾用例，随后显式开启并单独执行，2 passed。已有场景包括退出 0 缺协议结果、结果被拒、响应丢失重放、旧 API/未协商执行拒绝、结果拒收迁移及资源清理不提前确认。此次不新增数据库迁移；`c14d09e` 仍需随完整发布按迁移手册部署。全项目覆盖率与生产端到端验收留在 5.4/5.3。
