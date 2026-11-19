# 历史批次导入向导验证（2026-11-19）

实现只读历史草稿接口，按当次保存的任务/服务快照恢复可编辑提交；列表与详情均有“基于此批次创建”。测试覆盖宿主机/容器模板、多服务、两个实例、原固定槽位、不同任务参数/资源、0/false/空串、无限时长、集合后来修改、模板失效、缺失快照、越权、只读无数据库写入，以及导入编辑后预检/提交幂等、原记录不变。

- 新接口测试 6 passed（19.73 秒）。浏览器关联回归 batch-rerun、run-wizard、batch-monitoring：24 passed（40.0 秒），包括异步默认值、旧实例数量需填写、错误重试、迟到导入响应和重新导入清空草稿。
- TypeScript 类型检查、Vite 构建、OpenSpec strict 和差异检查通过；构建保留 500 kB chunk 警告。
- 统一覆盖率命令：`PYTEST_ADDOPTS='--import-mode=importlib -k "batch_rerun or orchestrations or monitoring or batch_deletion"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/batch-rerun`。
- 结果 42 passed、3 skipped、1834 deselected（144.26 秒）；[完整分母覆盖率](coverage-summary.md)未达门槛，全部 Python 35.40% / 15.48%，任务集合与编排 75.44% / 55.79%。未降低门槛或排除代码。前端未插桩与独立进程未合并，不宣称已统计覆盖率。

## 失败与跳过证据

首次后端 1 failed / 4 passed：测试误用 main.engine，实际引擎来自 SessionLocal.kw['bind']，已修正；首次浏览器 1 passed / 1 failed：未等待异步执行时限加载即点击下一步，收到“请先读取平台执行时限上限”，测试改为等待加载结果。复测通过，生产仍保留缺失时限时阻止预检的行为。原输出留在本地 .runtime/batch-rerun；失败栈的测试鉴权对象不提交，不复制凭据。

跳过：

- backend.tests.integration.test_monitoring_race.test_bulk_single_and_replay[1]: dedicated PostgreSQL required
- backend.tests.integration.test_monitoring_race.test_bulk_single_and_replay[10]: dedicated PostgreSQL required
- backend.tests.test_process_log_migration.test_process_log_migration_and_batch_deletion: requires disposable PostgreSQL server

## 交付边界

这是本地测试 API/临时数据库及浏览器模拟接口验收，不是真实模型或生产平台验收。功能不修改 Worker/Runner，不增加数据库迁移；尚未部署。整体升级若包含此前其他迁移，仍应核对仓库 head。导入不能绕过当前参数、模板权限、实例数或资源准入校验。原批次不被修改。
