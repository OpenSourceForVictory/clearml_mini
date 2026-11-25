# 控制对账凭据轮换专项

在隔离 PostgreSQL 上，`test_recovery_control.py::test_control_reconnect_after_worker_credential_rotation` 通过真实 FastAPI Worker 鉴权路由验证：原控制观察先提交；轮换节点凭据后，旧凭据返回 401；新凭据重放原序列得到 `duplicate=true`，随后同一执行 ID、分配代次和进程身份的下一序列继续 `track`。Run 未终结、未清理，不因旧凭据失败创建新执行。

2026-11-25 使用 `LOG_TEST_DATABASE_URL` 指向专用测试 PostgreSQL，单项连续运行 3 次，均为 **1 passed**。首次实现时曾遇隔离测试中调度锁竞争导致瞬时 503，未计为通过；用例改为有界重试 503 后三次通过，401 仍必须直接拒绝。凭据在测试夹具中生成，不属于现网节点轮换演练。此项不能单独证明完整断联恢复或生产启用。
