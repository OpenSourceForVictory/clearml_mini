# 历史批次业务参数预填与提交验证

触发条件：历史批次的每项任务快照均保存了旧的最终业务参数。此前在客户端步骤填写同名公共参数后，确认页可显示新输入，但提交时任务单项旧值优先，运行配置仍是旧值。

修正后，导入时把历史任务共同的业务参数预填到客户端步骤，并从单项草稿移除；不同值仍保留在逐任务清单。用户修改预填值后，沿用普通向导的预检、提交和参数优先级，未修改的单项差异不变。无类型化旧模板可编辑客户端共同参数 JSON。单项值仍优先于公共值，界面默认展开预检的逐项有效参数供核对。历史批次和模板定义不变。

界面手工验证：打开“评测运行与结果”，对已有批次点击“基于此批次创建”；在客户端步骤修改已预填的共同业务参数（例如历史值为 `/business/old` 的 `output_dir` 改为 `/business/new`）。到“检查与提交”核对展开的“有效业务参数与来源”，每项任务应显示新值；提交后进入新批次“运行配置”，逐项确认参数仍是新值。若历史各任务原值不同，先在任务清单逐项修改，再在预检中核对。此入口保留在批次列表和批次详情中。

验证：

- `frontend`: `npx playwright test tests/batch-rerun.spec.ts tests/run-wizard.spec.ts tests/orchestration-wizard.spec.ts`，37 项通过；包含公共修改、逐项修改、旧模板 `output_dir`、历史共同值预填、服务端参数和原向导回归。
- `frontend`: `npm run build`，通过。前端未做覆盖率插桩，不能宣称前端行或分支覆盖率。
- `backend`: `.venv/bin/python -m pytest backend/tests/test_batch_rerun.py -q`，8 项通过；新增断言从新批次详情取得任务 ID，再读取任务运行快照，确认 `output_dir` 是新值。
- `git diff --check`，通过。无数据库迁移、Worker 或 Runner 代码变更。

覆盖率脚本 `.venv/bin/python -m scripts.test_coverage --output coverage-artifacts/rerun-prefill-20261128` 在收集阶段因两个 `test_bundled_onboarding.py` 同名模块发生 import file mismatch，退出码 2；该次生成的低覆盖率报告不是有效的全量测试覆盖率。改用 `PYTEST_ADDOPTS=--import-mode=importlib` 后可继续收集，但完整套件运行耗时较长，本轮以相关专项测试完成验证，未声称满足全项目门槛。

这里的业务参数值验证使用测试模板和模拟 API；未在用户的其他部署做真实模型评测，也未验证模板基础命令中硬编码的输出路径是否随业务参数改变。旧式无参数映射模板的 `output_dir` 只作为业务输入传递，不自动追加到 argv。
