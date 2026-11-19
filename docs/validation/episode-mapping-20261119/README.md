# 宿主机单回合参数映射修复验证

2026-11-19；节点组件 0.21.2，协议仍为17，无数据库迁移。未部署到现场节点。

## 失败与修正

宿主机 per_episode 的 argv/env 平台映射在循环前按外层任务解析，导致 child input 为1回合但业务参数仍为总量，种子和目录同样不一致。Worker 的占位符也会在创建时提前固定。现场“只显示第1回合运行”的原因仍需核对真实模板；本地复现不等同于现场确定根因。

[before.log](before.log) 保留新增6个用例在旧实现的失败：direct、普通命令和 sdk-command × argv/env 映射。修复后按单回合上下文绑定，外层计数/事件保持原样；手工写死的业务参数不自动修改。

## 结果

- [after.log](after.log)：176通过、31跳过，45.19秒。
- [contracts.log](contracts.log)：86通过，60.68秒；参数、SDK/API事件契约和升级包回归。
- [conflicts.log](conflicts.log)：2通过；参数映射与显式环境冲突时，不启动业务，记录ERROR。
- 合计264通过、31跳过（以上选择互不重复）。新增18个用例包含回合输入一致性、Worker准备→真实业务子进程→事件上传、整任务/Docker入口语义回归及失败边界。
- 第二回合阻塞等待时，协议1/2均已能上传第1回合COMPLETED及第2回合STARTED，证明不必等整任务结束。
- 前端 TypeScript 检查和 Vite 构建通过，仅保留已有 >500kB bundle 警告。当前 shell 无 npm，改用已安装 Node 直接运行 tsc 与 vite。SDK ZIP 从源码重新生成，离仓库解压自检通过。
- OpenSpec 严格验证通过；初次发现追加任务编号与旧8.1冲突，已改为10.1/10.2。

## 测试命令与边界

主测试使用 `PYTEST_ADDOPTS=--import-mode=importlib`，运行 `.venv/bin/python -m coverage run --rcfile=backend/tests/project.coveragerc -m pytest`，选择：

```text
backend/tests/test_host_shell_episode.py
backend/tests/test_episode_mapping_pipeline.py
backend/tests/test_command_sdk.py
backend/tests/test_host_backend.py
backend/tests/test_host_backend_lifecycle.py
backend/tests/test_generic_commands.py
backend/tests/test_host_working_directory.py
backend/tests/test_log_capture.py
backend/tests/test_log_sender.py
backend/tests/test_integration_bundle.py
```

随后同一代码版本通过 `coverage run --append` 运行 task_input、parameter_contract、sdk_reporting_contract、worker_upgrades 和新增的 environment_conflict 用例。报告按 scripts.test_coverage 相同 coveragerc、source分母及 scripts.coverage_report 规则生成，见 [覆盖率](coverage.md)。仅跑相关测试，全项目行/分支31.34%/16.08%，门禁未达到，不能声称本次全项目覆盖率达标；未合并之前不同代码版本数据。

业务子进程真实执行，但未统一插桩到覆盖率；前端未插桩。31个跳过为24个真实Docker矩阵及7个systemd环境用例。新增管道测试替换Worker上报HTTP接收端，API契约由独立测试验证；不冒充真实平台端到端、GPU/NPU模型或远端节点验收。本轮没有跑全量测试，没有更新任何现场节点。
