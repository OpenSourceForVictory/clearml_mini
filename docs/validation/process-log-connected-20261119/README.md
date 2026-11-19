# 独立日志接线验证（2026-11-19）

用户确认整机日志默认不限制，跨账号配额通道不启用。接通真实子进程采集、执行目录 JSONL、Worker 私有历史登记和独立读取线程、API 请求邮箱、界面抽屉。Worker 0.21.6，协议 17；没有新增 Alembic revision。未部署平台、未更新任何已纳管 Worker。

## 模式矩阵

| 执行方式 | 协议 | 启动及日志行为 | 验证 |
| --- | --- | --- | --- |
| 宿主机逐回合 | 普通命令 / 直接协议 / SDK 命令适配 | 每回合进程、独立 episode 日志；失败 stop/continue | 15 项 |
| 宿主机整任务 | 普通命令 / 直接协议 | 一次进程，输入 episodes=3，business 公共日志 | 新矩阵 10 项 |
| 宿主机整任务配置 | SDK 命令适配 | SDK 自身循环启动，episode 日志；宿主机 per_episode 的 continue 不套用 | 新矩阵 5 项 |
| Docker | 普通命令 / 直接协议 | 一次进程，输入 episodes=3，business 公共日志 | 新矩阵 10 项 |
| Docker | SDK 命令适配 | SDK 逐回合调用业务子进程，episode 日志；失败停止 | 新矩阵 5 项 |

每组覆盖正常、第二次非零退出、缺少协议结果，以及 stop/continue 配置的有效或不适用行为。普通命令/整任务进程退出 0 不会因缺协议输出被采集器改成非零；协议汇总仍由原路径判断。新矩阵 30 项通过（其中 15 项真实 Docker），旧矩阵 15 项包含在关联回归中。不是 45 项之外再把原 15 重复计数。

## 自动化结果

- 关联回归 205 passed / 3 skipped / 1734 deselected，包含新采集/API/Worker 接线、原日志、环境、参数映射、执行恢复及路径测试。命令：`LOG_TEST_DATABASE_URL=<一次性 PostgreSQL> PYTEST_ADDOPTS='--import-mode=importlib -k "process_log or log_capture or host_shell_episode or episode_mapping_pipeline or image_runtime or worker_execution_recovery or execution_locations"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/process-log-connected-final`。
- 此后增加矩阵，命令：`PROCESS_LOG_TEST_IMAGE=mini-eval-runner:dev .venv/bin/python -m pytest --import-mode=importlib backend/tests/test_process_log_pipeline.py -k launch_modes -q`，30 passed。Docker 无网络、以当前普通 UID 执行，挂载当前 Runner 源码，工作目录 /adapter；没有重建或验证发布镜像。
- 浏览器 13 passed：独立日志、批次监控、服务编排。包含过滤、早期页、缺口、下载、窄屏、Escape、焦点恢复和关闭停止正文轮询。
- 独立 PostgreSQL 迁移测试 1 passed，未操作生产数据库。TypeScript/Vite 构建及 OpenSpec strict 通过。
- 统一覆盖率门槛未达到：全部 Python 行 39.59%、分支 18.07%；目录和关键功能比例详见 coverage-summary.md。新 30 项独立运行没有并入该统计，前端及未插桩子进程、Docker/GPU/NPU 子进程均不宣称已计入覆盖率。

## 真机小程序

- 本机宿主机 GPU：两个 CUDA 子进程分别分配/释放 32 MiB，独立回合文件闭合。
- 非 root Docker：stdout/stderr 的服务 scope 采集、关闭和保留文件检查通过，无加速计算。
- 29 节点 NPU：可见卡 7、逻辑设备 0，ACL 分配/释放 1 MiB，service scope 闭合并读取验证；临时目录自动清理，未改 Worker/systemd 服务。

这些是运行和日志链路小程序，不是 torch 模型评测；NPU 测试没有通过平台调度启动长期服务。全平台发布、同节点不同卡双服务的完整页面验收、10000 回合和慢盘/多用户连接池压测仍未完成。

## 仍未完成的设计项

OpenSpec 复合任务仍保留未完成标记：预算期望/生效 revision 管理界面、超过 8 MiB 下载的分片回退、完整索引崩溃恢复及压力验收。当前只保证已测试的部分写入尾部处理，不宣称所有淘汰/索引崩溃时序已恢复。节点不在线、文件损坏或清理竞争会显示超时/读取失败/busy，原诊断日志仍可用。没有据此降低原验收标准或归档整项设计。

相关操作文档同步：execution-logs、host-execution、worker-setup、worker-upgrades、batch-monitoring、界面指南及项目 Skill 的 project-map。

## 原生 Python SDK 补测（2026-11-19）

`test_native_sdk_process_logs` 新增 25 项，业务直接继承 EvaluationClient，并调用 runner.sdk.run；模板使用真实支持的 `integration_mode=direct`（界面“直接评测协议”）。不是通过 sdk-command 的结果文件间接模拟原生 SDK。

5 种运行配置 × 5 种结果：宿主机整任务 stop/continue、宿主机逐回合 stop/continue、Docker 整任务；结果为正常、EpisodeResult(False)、抛出异常、返回非法结果、SystemExit(7)。整任务配置中的 continue 不扩展为逐回合继续；原生 SDK 的 SystemExit 不生成当前回合 ERROR 事件，宿主机外层逐回合包装仍记录对应非零退出。此项如实验证现有行为，不掩盖差异。

检查参数/环境传递、episodes=3 或单次=1、seed 2147483647→0→1、最终汇总、业务失败与执行失败区分、内外事件不重复、stdout/stderr 来源、business 公共日志或 episode 独立日志。实际 Docker 为普通 UID、断网、注入当前源码，不是发布镜像或平台全链路升级验收。

首次审查发现测试填入了 API 不支持的 integration_mode=sdk；已改为 direct 并重跑，最终新增 25 项通过。没有修改生产执行逻辑。单项输出见 native-sdk-tests.txt；整份接线文件回归 81 项通过、无跳过（包含新 25 项及先前用例），统一口径覆盖率见 native-sdk-regression.txt 和 native-sdk-coverage.md。未插桩的业务子进程和容器不计入覆盖率；覆盖率门槛仍未达到，不以测试通过数替代覆盖率。

同时补充 worker-upgrades、execution-logs、界面升级指南和 Skill：SDK 随节点组件包更新；提交仓库代码不会自动更新节点，旧式业务镜像需独立更新。界面指南需随前端部署才在现场生效。本次不升级平台或节点。
