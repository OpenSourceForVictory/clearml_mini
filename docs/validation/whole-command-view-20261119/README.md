# 整任务普通命令显示验证（2026-11-19）

触发条件：普通命令整任务启动，配置多个回合，界面仍有回合表/分页，且 episode_reporting 标记可能错误影响展示。修正为按模板实际进程模式提供 detail_mode；仅整任务普通命令显示整次执行、配置回合数和任务退出码，API 不生成虚构计划回合。SDK 命令适配、协议单进程及逐回合模式保留回合展示，历史记录不删除。

普通命令整任务按平台执行退出码统计正常/异常，非零可能来自初始化或包装器；不冒充业务子进程退出证据。协议模式仍不从包装器非零推断业务异常。超时、取消、失败等生命周期单独保留，未取得退出码不能判定正常。

- Python 定向回归：`pytest --import-mode=importlib backend/tests/test_result_outcomes.py backend/tests/test_episode_visibility.py -q`，54 passed，1 warning，38.44 秒。
- 统一口径：`PYTEST_ADDOPTS='--import-mode=importlib -k "result_outcomes or episode_visibility"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/whole-command-view`，54 passed，1819 deselected，1 warning，40.20 秒；[覆盖率报告](coverage-summary.md)。完整分母未改变，全部 Python 行 31.29% / 分支 8.25%，业务执行与结果 26.50% / 10.12%，门槛未达到，不能宣称全量测试或覆盖率达标。未插桩前端与独立子进程不纳入覆盖声明。
- 浏览器首次 6 passed / 2 failed：测试模拟未提供前一变更新增的 execution-location 返回结构，任务排查组件无法正常显示。已补齐模拟数据；[首次失败输出](browser-first-failure.txt)已保存；完整运行目录不提交。第二轮执行中更新了生命周期中文文案，旧测试进程仍断言 TIMED_OUT，导致 1 项失败；已使用最终源码重新测试。

只验证平台统计、API 与模拟数据页面，不是真机 GPU/NPU 模型验收。无 Worker/Runner 修改，无本次数据库迁移；未部署。此前其他未发布变更可能包含迁移，整体升级仍须按迁移文档核对 head。日志配额通道设计缺口仍待讨论。

最终浏览器回归 `batch-monitoring.spec.ts`：8 passed（27.5 秒），覆盖任务排查、批次入口、历史尝试、协议回合、整任务退出 0/7/无退出码和中文超时状态。TypeScript 检查与 Vite 构建通过；构建保留大于 500 kB chunk 的警告。OpenSpec strict 与 git diff --check 通过。
