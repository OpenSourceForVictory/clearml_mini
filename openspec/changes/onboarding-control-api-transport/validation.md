# 验证

- `.venv/bin/python -m pytest backend/tests/test_onboarding_probes.py backend/tests/test_onboarding_recovery_edges.py backend/tests/test_node_cleanup.py -q`：82 passed，1 条外部依赖弃用警告。
- 纳管相关 10 个测试文件以 `coverage run --branch --source=onboarding.executor` 采集：206 passed、8 skipped；8 项均来自 `test_onboarding_db_isolation.py`，原因是需要可销毁的 PostgreSQL 测试服务。`onboarding/executor.py` 301 条语句中 300 条覆盖（99.67%），104 个分支中 84 个覆盖（80.77%），综合覆盖率约 95%。这仅是单模块专项采集，不代表 `scripts.test_coverage` 口径下的全项目或目录门槛。
- 故障复现：在临时 `HTTP_PROXY` 指向不可用代理时，原默认 `urllib` 对内部 `/healthz` 请求失败；显式空代理处理器得到 HTTP 200。本机正式执行器没有代理变量，所以不能据此确认其他部署的 504 原因。
- 其他平台发生的 504 尚未取得其网关与执行器日志；更新执行器后应以新增路径日志定位。此处未完成远端纳管验收。
