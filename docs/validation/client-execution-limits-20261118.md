# 客户端总执行时限专项验证（2026-11-18）

## 修正范围

旧请求模型和提交界面固定最多 3600 秒，即使平台配置更大也不能提交；执行阶段又取配置与快照的最小值，降低配置会缩短存量任务。现在从认证接口读取上限，提交按当前上限明确拒绝非法值，执行按已保存快照。启动、租约、总执行超时分别提示。

## 验证结果

- `PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_client_execution_limits.py backend/tests/test_execution.py backend/tests/test_scheduling.py -q`：19 通过，1 条 Starlette/AnyIO 弃用警告，无跳过。
- `playwright test tests/run-wizard.spec.ts tests/orchestration-wizard.spec.ts tests/command-authoring.spec.ts tests/image-runtime.spec.ts`：22 通过，无跳过。覆盖 7200 秒实际提交、7201 秒拒绝、配置读取失败与重试保留草稿，及相关普通命令、容器、服务编排提交。
- TypeScript `tsc --noEmit`、OpenSpec 严格校验与 `git diff --check` 通过。

## 失败与限制

首次扩展浏览器回归时，旧普通命令与镜像运行测试未模拟新增 execution-limits 接口，返回默认空数组，向导按设计阻止继续，测试失败；该轮中断后补齐接口契约，再运行上述 22 项通过。另一次文档编辑脚本因从 frontend 工作目录读取仓库相对路径失败，未产生修改，已在仓库根目录重新执行。

遵照本轮仅专项测试要求，未运行全量 `python -m scripts.test_coverage`，没有本轮目录/关键功能行与分支覆盖率达标结论；未插桩前端及独立进程不计覆盖率。API 测试使用临时数据库，浏览器使用模拟接口；未部署、未执行真实 GPU/NPU 业务。无数据库迁移，更新 API、调度器与前端即可，Worker 无需为此升级。
