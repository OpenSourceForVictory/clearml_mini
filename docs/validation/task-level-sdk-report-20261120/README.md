# 任务级 SDK 结果上报定向验证（2026-11-20）

本次功能新增普通整任务程序的一次性 SDK 业务结果上报。业务结果由 SDK 写入 execution 专属工作目录，Worker 使用自身凭据转发给 API；程序退出状态与业务结果独立保存和展示。

## 定向验证

- 后端：`backend/tests/test_task_result_reporting.py`、`test_command_tasks.py`、`test_result_outcomes.py`、`test_reports.py`、`test_integration_bundle.py`，73 项通过，1 项既有 Starlette 弃用警告。
- 前端：`frontend/tests/template-guidance.spec.ts` 与 `batch-monitoring.spec.ts`，53 项通过。首次运行有一项新增提示文字断言与实际文案不一致；修正断言后全组通过。
- 前端 `tsc --noEmit` 与 Vite 生产构建通过。Vite 报告现有主 chunk 643.31 kB，超过 500 kB 提示阈值。
- 定向 Python 覆盖率使用 `backend/tests/project.coveragerc`，仅运行上述后端测试。选定模块聚合 43.41% 行覆盖；该数字包含本次回归未涉及的大量既有代码，不能作为全仓或功能组门槛结论。`runner/task_result_contract.py` 为 90.48%，`backend/app/result_views.py` 为 100%，其余模块明细可由忽略目录 `.runtime/task-level-sdk-report-targeted.coverage` 重现。

## 行为覆盖

SDK 对同内容并发/重复上报幂等，对不同内容冲突；拒绝无效类型、非有限指标、过长摘要、未知字段及缺少工作目录。Worker 按 execution ID 读取独立结果，网络超时后重试，持久化成功游标，并对不同执行目录隔离。API 验证 Worker 身份、启用模板、旧协议准入、重复请求和冲突请求；即使业务结果为失败，正常进程退出仍保持程序执行成功状态。Docker 入口样例和宿主机命令入口均由本机隔离子进程验证。

## 未执行范围

用户要求暂不运行全量测试，因此 `python -m scripts.test_coverage` 全仓命令已在约 17% 进度时中止；中断不产生全仓覆盖结论。未进行平台部署、远程节点验收、真实 GPU/NPU 运行或真实模型评测。SDK 示例中的 `demo_pass` 是演示条件，不代表业务评测验收。

定向测试通过可证明本次触达的契约和 UI 行为；不能替代统一全仓覆盖率门槛或真机/生产验收。
