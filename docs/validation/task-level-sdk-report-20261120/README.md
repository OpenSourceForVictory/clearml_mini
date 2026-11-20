# 任务级 SDK 结果上报定向验证（2026-11-20）

本次功能新增普通整任务程序的一次性 SDK 业务结果上报。业务结果由 SDK 写入 execution 专属工作目录，Worker 使用自身凭据转发给 API；程序退出状态与业务结果独立保存和展示。

## 定向验证

- 后端：`backend/tests/test_task_result_reporting.py`、`test_command_tasks.py`、`test_result_outcomes.py`、`test_reports.py`、`test_integration_bundle.py`，73 项通过，1 项既有 Starlette 弃用警告。
- 前端：`frontend/tests/template-guidance.spec.ts` 与 `batch-monitoring.spec.ts`，53 项通过。首次运行有一项新增提示文字断言与实际文案不一致；修正断言后全组通过。
- 前端 `tsc --noEmit` 与 Vite 生产构建通过。Vite 报告现有主 chunk 643.31 kB，超过 500 kB 提示阈值。
- 定向 Python 覆盖率使用 `backend/tests/project.coveragerc`，仅运行上述后端测试。coverage.py 默认报告的选定模块综合覆盖率为 43.41%（行与分支合并，并非独立行覆盖率）；该数字包含本次回归未涉及的大量既有代码，不能作为全仓或功能组门槛结论。`runner/task_result_contract.py` 综合为 90.48%，`backend/app/result_views.py` 为 100%，其余模块明细可由忽略目录 `.runtime/task-level-sdk-report-targeted.coverage` 重现。

## 行为覆盖

SDK 对同内容并发/重复上报幂等，对不同内容冲突；拒绝无效类型、非有限指标、过长摘要、未知字段及缺少工作目录。Worker 按 execution ID 读取独立结果，网络超时后重试，持久化成功游标，并对不同执行目录隔离。API 验证 Worker 身份、启用模板、旧协议准入、重复请求和冲突请求；即使业务结果为失败，正常进程退出仍保持程序执行成功状态。Docker 入口样例和宿主机命令入口均由本机隔离子进程验证。

## 未执行范围

用户要求暂不运行全量测试，因此 `python -m scripts.test_coverage` 全仓命令已在约 17% 进度时中止；中断不产生全仓覆盖结论。初始定向验证未部署，随后应用户要求完成下述真实宿主机批次验证。未进行真实 Docker 容器、远程 NPU 节点或真实模型评测。SDK 示例中的 `demo_pass` 是演示条件，不代表模型质量验收。

定向测试通过可证明本次触达的契约和 UI 行为；不能替代统一全仓覆盖率门槛或真机/生产验收。

## 真实宿主机批次与在线页面验证

2026-11-20 发布 API/前端镜像 `mini-eval-api:task-sdk-a203cc0`，保留当前 Compose 配置，发布前确认所有节点无持有执行，临时停止准入并在发布后恢复。无需数据库迁移。通过平台在线升级 API 仅升级本机 `local4090-151` 至 Worker 0.21.13 / 协议 18，升级记录 `ed5b9a0c-2ceb-4542-b032-c65e5f5246c8`，状态 SUCCEEDED；其他节点保留原版本。

批次名称：**SDK可运行示例｜真实宿主机批次｜业务成功与失败**。批次 ID：`e78539b0-2772-40e1-b957-77f06d5c6fa9`，界面入口 `/#/cs/batches/e78539b0-2772-40e1-b957-77f06d5c6fa9`。

模板为普通整任务、`sdk-task-result`、宿主机运行，使用已批准的 `template_demo_cwd` 环境。执行命令为 `python3 examples/integration/task_result_client.py`，实际 Python 为批准环境的 `/home/dgh/Code/clearml_mini/.venv/bin/python`。直接运行仓库可运行示例，没有替换业务脚本或向 API 人工写入结果；示例通过 `MINI_EVAL_INPUT` 的 business.demo_pass 参数决定上报结果。

| 用例 | demo_pass | 程序退出码 | SDK 业务结果 | 指标 | 清理 |
| --- | --- | --- | --- | --- | --- |
| business-success | true | 0 | 成功 | score=1 | 已确认 |
| business-failure | false | 0 | 失败 | score=0 | 已确认 |

两个任务由真实 Worker 在 `local4090-151 / GPU 0` 上顺序启动。GPU 卡参与平台分配，示例没有执行 GPU 算术。执行 ID 分别为 `375c9f34-4e6a-4409-a86a-da0e1b372a36`、`5be3cdbe-6925-44e8-811f-64803cef89c0`。验证链路为批次预检与提交 → Worker 领取/启动 → 示例调用 SDK → Worker 转发 → API 保存 → 在线批次页面展示。

Playwright 访问真实 API 提供的在线页面，未 mock 网络，断言程序正常退出率为 100%（2/2）、任务级业务成功 1 / 失败 1、各行指标及节点位置正确，页面无 JavaScript 错误。API 另核对两项退出码均为 0、结果正确、没有伪造回合、cleanup_confirmed=true。批次与模板保留，供用户在界面查看。

首次验收驱动器误从 jobs.attempts 读取不存在的 exit_code 字段而抛出 KeyError；业务批次已经正常完成。随后从执行详情 episodes 接口返回的退出码字段和批次 outcomes 核对同一批次，没有创建重复任务。配置、API 回执和浏览器截图保存在忽略目录 `.runtime/task-sdk-live/`；凭据不进入仓库。
