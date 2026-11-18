# 客户端连通性诊断验证（2026-11-18）

触发：现场服务端记录 405，但客户端仅有 client_service_unreachable，原实现丢弃异常详情且无专门诊断日志。HEAD 405 原本应通过；本变更不宣称已确定现场失败根因。

修复前新增 11 个用例失败：真实本地 HTTP 405 已可通过，但缺少 status=405 日志；其余诊断测试因待实现的 _probe 接口不存在失败。修复后专项测试命令：

```bash
PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_client_connectivity_diagnostics.py backend/tests/test_readiness_probes.py backend/tests/test_service_readiness_execution.py -q -rs
```

结果 27 passed，22 skipped。跳过原因 requires Docker and delegated systemd（HOST_EXECUTION_TEST 未启用），不是业务验证成功。测试覆盖本地 HTTP 405、代理忽略、WS 握手/认证挑战、超时/取消、分类与脱敏、IPv6/非法目标及默认 3 秒；失败分类部分采用注入异常，不代表现场网络已修复。TypeScript 检查、OpenSpec strict 校验及 git diff --check 通过。

未运行全量测试、统一目录行/分支覆盖率统计、真机 NPU/GPU 业务或部署。不能宣称达到覆盖率门槛。已纳管客户端需更新 Worker 后才能看到新日志；无需数据库迁移。日志只含目标 origin，不输出路径、查询、凭据或原始异常文本；任务错误保留安全分类。
