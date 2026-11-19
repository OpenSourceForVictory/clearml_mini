# 程序执行与协议回合结果分离验证

## 结果

Worker/Runner 源码版本 0.21.5、协议仍为 17。无数据库迁移，本轮未部署 API 或升级节点。

- 最终 Python 关联回归：**279 passed / 28 skipped / 0 failed / 1549 deselected**，171.23 秒。pytest 返回 0，见 [原始日志](python.log)。
- 前端全套：**139 passed / 10 skipped / 0 failed**，2.5 分钟，见 [日志](frontend.log)。之后移除报告比较页的旧重复成功率摘要，相关 4 项再次通过，见 [报告页复测](report-ui.log)。
- TypeScript 类型检查和 Vite 构建通过；保留 chunk 大于 500 kB 提示，见 [构建日志](frontend-build.log)。
- 新增共享统计模块 backend/app/result_views.py：65/65 行、24/24 分支，均为 **100%**。
- 定向运行采用完整项目分母：总行 **43.05%**、总分支 **21.72%**，**项目覆盖率门槛未通过**，scripts.test_coverage 因门禁返回非零。不得称本轮全项目覆盖达标，不合并上一轮或其他版本的数据。详见 [覆盖报告](coverage.md)。

## 执行与验证范围

通过 `.venv/bin/python -m scripts.test_coverage --output coverage-artifacts/episode-result-diagnostics-verified` 运行；`PYTEST_ADDOPTS` 设置 `--import-mode=importlib` 和下列 `-k` 选择表达式，仅筛选测试，不改变项目代码分母或覆盖阈值：

```text
result_outcomes or command_sdk or host_shell_episode or episode_visibility or reports or monitoring or sdk_reporting_contract or episode_mapping_pipeline or integration_bundle or cs_protocol or worker_execution_recovery or host_supervisor_output or runner_entrypoint_contracts or generic_commands
```

验证真实本地业务子进程 stdout/stderr、缺失/重复/非法 final、SDK 结果文件缺失/非法/超限/符号链接、已知错误无重复堆栈、未知异常保留、停止/继续策略；接口验证三次正常退出与三次结果异常独立计数、最新重试与历史分离、跨页汇总、CSV/报告一致、普通命令比较返回 200。原生 SDK 无独立回合退出码、单进程不重复计数、非零包装器码不伪造业务退出均有分类测试。SDK 下载包重建并验证脱离仓库运行。

[失败证据](before.md) 及两轮旧异常类型断言失败原始日志均保留。新测试要求明确诊断及无成功结果，未删除失败场景。最终集合重新完整执行后为 279 通过。

[跳过明细](skips.md) 包含真实 Docker 矩阵、隔离 PostgreSQL 和专用进程执行开关。这里的本地子进程、测试 API 和浏览器模拟请求不代表真实 GPU/NPU 模型验收，也不代表真实容器环境已全部验证。前端未插桩；未插桩独立进程不计入 Python 覆盖率。

## 已知数据边界与上线要求

普通命令协议结果不适用；原生 SDK 回合无独立进程时退出码不适用。旧单进程记录若只有非零包装器退出码，无法证明业务进程异常退出，统计保留未知并显示包装器原码。旧通用 ERROR 不重建精确结果错误类型。

API/前端更新后可读取新 outcomes；节点 Worker/Runner 更新后新执行生成明确协议诊断。业务镜像自带 Runner 的需重建对应镜像。旧 progress/summary 字段兼容保留，新界面及报告 outcomes 使用统一分类。指南、宿主机操作、批次监控/报告、Worker 升级文档及项目 Skill 索引已同步。
