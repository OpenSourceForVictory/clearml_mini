# 代码覆盖率规范

本项目按目录和关键功能分别检查，禁止用少数模块的高覆盖率代表全项目。覆盖率衡量执行到的代码，不替代业务断言、并发测试或真机验收。

2026-11-18 宿主机/容器全量回归、真实启动矩阵与 PostgreSQL 并发失败证据见 [验证记录](validation/host-container-full-20261118/README.md)。

## 强制门槛

| 范围 | 行覆盖率 | 分支覆盖率 |
| --- | ---: | ---: |
| 每个 Python 目录 | ≥80% | ≥70% |
| 调度与资源、节点纳管与升级、业务执行与结果 | ≥90% | ≥80% |
| 其他功能分组 | ≥80% | ≥70% |

控制面背压变更把 `backend/app/db_budget.py` 纳入“调度与资源”，把 `worker/backpressure.py` 及执行 API `backend/app/execution.py` 纳入“业务执行与结果”；它们同时保留在目录总量中，关键功能仍按 90% / 80% 验证，不降低门槛。

配置在 `backend/tests/coverage-floors.json`，CI 与本地使用相同规则。任何测试失败或任一门槛未达到均返回非零；既有代码未达标也不会自动豁免。新增功能必须随代码提供正常、边界和失败路径测试。不得通过排除未测文件、mock 掉需要验证的行为或降低门槛凑数字；调整统计范围/门槛需有明确需求和评审说明。

## 运行

在独立测试环境运行，禁止指向生产数据库。Python 3.11+，完整命令：

```bash
pip install -r backend/requirements.txt -r backend/tests/requirements-coverage.txt
python -m scripts.test_coverage
```

如果测试收集报告同名文件 `import file mismatch`（例如单元与 integration 目录均有 `test_bundled_onboarding.py`），使用 `PYTEST_ADDOPTS=--import-mode=importlib python -m scripts.test_coverage`，保留首次收集失败证据；不要为绕过冲突而排除整个测试目录。真实 PostgreSQL 专项另需隔离数据库环境变量，见各专项验证记录。

报告输出到 `coverage-artifacts/python/`（Git 忽略）：

- `summary.md`：按目录与功能的行/分支覆盖率、门槛失败项。
- `summary.json`：精确计数、要求与每文件未覆盖行/分支。
- `coverage.json` / `coverage.xml`：coverage.py 标准原始报告。
- `html/index.html`：逐文件彩色未覆盖代码。
- `tests.xml`：通过、失败、跳过的测试明细；`test-exit.json` 保存 pytest 状态。

`--output /path/to/report` 可设置输出目录；每次清理的只是该目录下本命令使用的覆盖率数据文件，不合并其他运行或其他代码版本的结果。CI 无论成功或失败都上传 `python-coverage` 产物。

## 统计规则与边界

Python 分母包含 `backend/app`、`worker`、`node_agent`、`runner`、`onboarding`、`scripts`，未导入的文件也计入。包括运维脚本；测试文件、前端、第三方依赖不在 Python 分母中。缺失整个统计目录报错；无分支的分组显示 N/A，不伪造分支执行数。

目录统计按覆盖行数/总行数加权，不能把文件百分比简单平均。功能映射在 `scripts/coverage_report.py` 中公开维护；同一个文件可以属于多个功能，功能百分比不能相加。总数每个文件只计算一次。

前端 TS/React 尚未统一插桩，因此没有前端行/分支覆盖率；Playwright 测试通过数不等于代码覆盖率。独立 subprocess、Docker 与真机进程未自动合并覆盖数据，父进程报告不会声称覆盖了这些进程。现有特权、Docker、迁移与 PostgreSQL 测试如未开启，其跳过状态保留在报告，不能描述为已验收。

PostgreSQL 集成测试需管理员准备一次性测试库、运行迁移并提供 TEST_DATABASE_URL；其他破坏性迁移/特权测试必须使用各自独立环境与显式开关，不因统计覆盖率而在生产机器启用。

## 不达标处理

先看测试失败，再看 summary 的低覆盖目录及 HTML 未覆盖行。优先补调度准入、资源释放、异常恢复、权限拒绝、SDK 结果校验等行为测试。不能仅为提高比例而编写无断言的执行测试。本规范的门槛是要求，首轮基线未达标时如实标记，不称为已完成质量验收。

## 本轮回归发现与验证边界

- 协议 14 Worker 注册曾因 API 枚举遗漏返回 422；注册契约已补齐，并由当前 Worker 版本注册测试约束。
- 下载的 SDK 包曾缺少通用命令解析模块；构建清单和下载 ZIP 已同步，测试校验包与源码一致并验证脱离仓库执行。
- SSH 纳管执行器遇到超过 4 MiB 的控制面响应时，原有 `Rejected`（继承 `ValueError`）被通用异常捕获，误报 `control_api_unreachable`。现在保留明确的 `control_response_limit`，无效 JSON 和网络失败仍返回脱敏的不可达错误。`test_onboarding_probes.py` 保留超限回归断言。

新增测试使用真实临时文件、业务子进程和 Unix socket；Docker、SSH、部署命令及外部 API 的故障注入测试只验证边界行为，不宣称生产节点升级、真实 GPU/NPU 或模型评测已验收。构建、控制节点部署、观察脚本均不访问生产系统。覆盖率未达门槛的目录继续由门禁报失败，不降低要求。

完整补测范围、失败证据和统计边界见 [本轮验证记录](validation/project-coverage/README.md)。真接口界面测试可在独立测试 API 上显式设置 `SUITE_LIVE_TEST_API`，运行 `frontend/tests/suites-live.spec.ts` 与 `frontend/tests/templates-live.spec.ts`。它们使用专用测试账号，创建模板和集合数据，禁止指向生产。

## 运维脚本与真实迁移补测

`test_smoke_runtime_scripts.py`、`test_smoke_farm_script.py`、`test_smoke_shared_gpu_script.py`、`test_two_host_acceptance_scripts.py`、`test_robotwin_queue_driver.py` 验证验收驱动器不会将错误回合、缺失产物、设备分配错误、重启重复执行、取消误伤、挂载错误等误报为通过。SSH、Docker、GPU、进程边界被隔离；这些用例不执行真实双机或 GPU 验收。三个双机脚本现在先检查隔离 API 地址，再启动测试监听服务，拒绝非隔离地址不会留下监听线程。

`test_fleet_acceptance_recovery.py` 使用真实检查点文件验证配置恢复不覆盖管理员后续变更；虚拟时间测试仅验证稳定性观察器的逻辑，不可作为两小时稳定性证据。

真实迁移脚本测试通过独立开关 `MIGRATION_DRIVER_TEST_URL` 启用，值为一次性 PostgreSQL **服务器**上的管理连接（需要 CREATE DATABASE 权限）。每个用例创建 `migration_driver_<随机值>` 数据库，只在该库执行历史升级、回填断言、受保护降级和再次升级，最终强制删除该临时库。禁止指向生产服务器；不要与普通应用数据库连接混用。CI 使用其已有的一次性 PostgreSQL service 并显式设置此变量。本地未提供时保留跳过，覆盖率门禁可能因此不达标；门槛不作豁免。

旧布局纳管的 `scripts/node_layout.py` 同时纳入 scripts 目录与“节点纳管与升级”关键功能统计，沿用原 90%/80% 门槛。若同名测试模块导致收集冲突，可设置 `PYTEST_ADDOPTS=--import-mode=importlib` 再运行原覆盖命令；不排除测试，保留首次失败报告。

机器删除与远程清理补测见 [验证记录](validation/node-removal/README.md)：`test_node_removal.py`、`test_node_cleanup.py` 检查身份重用、历史保留、清理回执和权限/失败路径；`test_resource_roles.py` 检查双角色计数与普通用户汇总权限。`onboarding/cleanup.py` 自动纳入现有“节点纳管与升级”统计。Agent 并发落盘测试在插桩与磁盘负载下原 5 秒等待不足，使用共享 30 秒截止时间，保留所有状态和持久化断言；没有调整覆盖率门槛。

回连恢复新增 `test_reconnect_resilience.py`，覆盖后台依赖阻塞/失败分类、心跳能力更新、可选能力与验收边界、超时配置、当前凭据继续验收、旧修订/发布拒绝及执行器重启恢复。部署测试严格校验新增超时变量白名单，仍禁止暴露端口/特权/无关依赖。完整记录见 [回连恢复验证](validation/resilient-node-reconnect/README.md)。

2026-11-18 复测将 `MIGRATION_DRIVER_TEST_URL` 指向一次性 PostgreSQL，与全量覆盖命令在同一进程树中启用已有真实迁移用例；不再仅单独运行后口头计入覆盖率。`TEST_DATABASE_URL` 同样仅使用此一次性环境，验证 Agent 并发修复。原 78.51% scripts 行覆盖率失败证据保留，不降低门槛。

日志 v2 的 worker/log_stream.py、backend/app/execution_logs.py 与 runner/log_capture.py 纳入“业务执行与结果”关键功能（90% 行 / 80% 分支）和各自目录分母；不排除新增日志路径。实际 systemd/Docker 子进程和浏览器未插桩部分不计入 Python 覆盖率。


## 2026-11-19 补充迁移验证

宿主机恢复全量后启用 3 项真实 PostgreSQL 迁移脚本测试，使用临时独立容器和数据库，验证结束已清理。相同产品实现的全量加补充覆盖数据达到全部目录与关键功能门槛；scripts 行 80.81%、分支 78.20%。保留初始全量失败及跳过，不能称第二轮全量通过。命令口径、边界与报告见 [验证记录](validation/host-recovery-full-20261119/README.md)。

修正后再次完整运行：Python 1629 通过/133 跳过/0 失败，前端 138 通过/10 跳过，真实迁移补测 3 通过。仅本轮全量加本轮迁移数据的累计覆盖门槛通过，默认全量脚本仍因 scripts 覆盖不足返回 1；详见 [本轮完整复测记录](validation/full-recheck-20261119/README.md)。

Worker 0.21.4 后再次全量测试：Python 1727 通过 / 104 跳过，前端 138 通过 / 10 跳过，均无失败。本轮直接启用一次性 PostgreSQL 的并发及迁移驱动测试，单轮覆盖率 90.00% 行 / 79.92% 分支，全部目录与关键功能组门槛通过。完整证据与跳过边界见 [测试记录](validation/full-pids-0214-20261119/README.md)。


程序执行与协议结果分离的定向回归：279 通过、28 跳过，新共享统计模块 100% 行 / 100% 分支；完整项目分母覆盖率 43.05% / 21.72%，门槛未通过，不作为全项目覆盖验收。`backend/app/result_views.py` 纳入业务执行与结果关键功能组，未降低阈值。失败、跳过及前端验证见 [记录](validation/episode-result-diagnostics-20261119/README.md)。

进程日志基础模块 `backend/app/process_logs.py`、`backend/app/execution_locations.py` 纳入“业务执行与结果”关键功能统计；Runner 契约沿用 `runner` 全目录统计，不排除尚未覆盖的分支。

## 独立日志故障回归

`backend/tests/test_process_log_failures.py` 补充子进程采集和 Worker 日志接线的失败/恢复用例。真实业务子进程由测试进程内的采集器管理，因此采集器本身可被标准 coverage 统计；业务子进程内部的 Python 仍没有自动合并，不能因启动了子进程就宣称 SDK 内部已统计。注入磁盘/API 故障用于验证降级和重试，锁冲突、子进程退出/回收使用实际系统调用。结果见 [专项验证](validation/process-log-failures-20261119/README.md)。

业务执行与结果功能组补测使用现有 FUNCTIONS 中的全部文件（包括 runner/*.py）作为分母，补充宿主机回合控制、日志存储失败、真实写进程中断、并发 scope、清理锁和 PostgreSQL 请求容量验证；范围和证据见 [功能组验证](validation/execution-group-logs-20261119/README.md)。隔离负载测试不替代生产容量评估；万回合用例验证的是已有历史索引，不是启动一万次业务程序。

最终关联回归：870 通过、40 跳过；业务执行与结果完整 38 文件分母为行 91.21%、分支 83.13%，达到 90%/80%。全仓其他目录未达标及真实节点矩阵跳过均保留，不宣称全项目达标；证据见 docs/validation/execution-group-logs-20261119（从仓库根目录定位）。

节点产物读取基础：`worker/artifact_reader.py`、`worker/artifact_access.py` 和已有 `backend/app/artifact_media.py` 纳入“业务执行与结果”关键功能统计；不排除尚未接线的代码。定向测试与全仓分母未达标证据见 [节点读取记录](validation/node-artifact-reader-20261119/README.md)。真实子进程未插桩不计入模块覆盖率。

2026-11-20 覆盖率补齐与日志配置收尾：统一全量 Python 2218 通过/101 跳过/0 失败，整体 91.73% 行、82.58% 分支，六目录与关键功能全部通过；浏览器 197 通过/10 跳过。新增 backend/app/log_policy.py 与 worker/log_policy.py 纳入业务执行结果分组，无分母豁免。保留基线失败、SDK 下载包遗漏、前端旧用例和热更新失败；详见[完整验证记录](validation/coverage-followup-20261120/README.md)。

## 功能正确性与覆盖率的边界

2026-11-20 冻结提交 `c659a63` 的最终全量回归：Python 2454 通过、0 失败、99 条件跳过，行覆盖率 91.99%、分支覆盖率 83.15%；调度与资源、节点纳管与升级、业务执行与结果关键组分别达到 97.65%/88.54%、95.67%/83.65%、96.29%/89.33%。前端 241 通过、10 条件跳过，未插桩，不计入 Python 覆盖率。[最终回归记录](validation/full-followup-20261120/README.md)保留测试边界和原始报告位置。

覆盖率只说明代码被执行到，不证明业务断言完整。全部历史变更、关键行为测试入口、真机证据及未发布/未验收边界统一见 [功能验收台账](validation/history-acceptance-20261120/README.md)。检查单必须区分测试资产存在、本版本实际执行通过、真机通过和部署通过；不能以单项替代其余结论。

回合产物目录归属（Worker 0.21.12）扩展专项：523 通过、66 跳过；新 manifest 96.72%/100%，读取器100%/100%，API共享读取93.81%/84.85%。全仓门槛及部分关联模块未达标，不沿用上一版本全量通过结论；见[专项证据与边界](validation/artifact-manifest-20261120/README.md)。

2026-11-20 批次优先级发布后的覆盖复测：在同一 `scripts.test_coverage` 统计口径下启用一次性 PostgreSQL 的 `TEST_DATABASE_URL`、`LOG_TEST_DATABASE_URL` 和 `MIGRATION_DRIVER_TEST_URL`，使用 `PYTEST_ADDOPTS=--import-mode=importlib` 避免既有同名测试模块收集冲突。最终 **2331 通过 / 126 跳过 / 0 失败**，耗时 1835.16 秒；总覆盖率 **92.00% 行 / 83.14% 分支**，`scripts` **80.81% / 78.20%**，六个 Python 目录及全部关键组门槛通过。数据库驱动用例创建并清理独立测试数据库；容器在测试结束后移除。首轮启用该环境时，4 个旧迁移测试夹具用当前 ORM 向旧 schema 写入 `priority` 而失败；将批次 fixture 改为只写历史 schema 已有字段，定向真实 PostgreSQL 迁移测试 5 项通过，之后完整复测通过。首轮失败、最终 JUnit 与覆盖率原始报告分别保存在忽略目录 `.runtime/batch-priority-coverage-postgres-20261120/` 和 `.runtime/batch-priority-coverage-postgres-recheck-20261120/`；126 条条件跳过仍保留在最终报告中。

全量之后按条件补跑新增记录：Docker 通用命令矩阵 24 项、真实 Docker 进程日志/服务日志 89 项、存储探测镜像 1 项、模板样例镜像 1 项，以及隔离 PostgreSQL 迁移和并发测试 7 项，均通过。这些是独立补跑，不回写 2331/126 的全量统计。补跑发现 endpoint 迁移测试把受保护降级后的版本写死为旧 revision；改成比较操作前后的 Alembic revision 后，7 项 PostgreSQL 测试全部通过。首次补跑中其余失败来自迁移子进程未继承 `PYTHONPATH`、race 数据库未预先迁移；使用正确隔离库初始化后通过。systemd user manager 在当前会话不可用，root 专用纳管和特权 mount namespace 条件未启用。详见[条件跳过续查](validation/batch-priority-skip-recheck-20261120/README.md)。

2026-11-20 任务级 SDK 业务结果上报的定向回归：后端 73 项、前端 53 项通过，Vite/TypeScript 构建通过。该次用户要求不运行全量套件，统一覆盖率进程约 17% 时中止；定向覆盖率不代表全仓或功能组门槛。详见[定向验证记录](validation/task-level-sdk-report-20261120/README.md)。

2026-11-23 Worker 0.21.14 当前版本全量：Python 2369 通过 / 126 条件跳过 / 0 失败，整体 92.02% 行 / 83.16% 分支，全部目录与关键功能组门槛通过。前端首轮指南定位歧义 1 项失败，修正断言后完整复测 243 通过 / 10 条件跳过；前端未插桩。使用一次性 PostgreSQL，未部署节点或补做真机验收。详见[本轮记录](validation/full-regression-20261123/README.md)。

执行时间功能将 backend/app/execution_timing.py、episodes.py 纳入“业务执行与结果”关键分组；runner/episode_timing.py 已由 runner/*.py 计入。门槛不变，新增采集与投影代码均计入目录分母。

2026-11-23 控制面五类预算专项：按用户要求暂不运行全量；使用同一 `backend/tests/project.coveragerc` 对相关测试采集分支覆盖率。仅可报告对应模块的专项覆盖情况，不可替代全目录或关键功能组门槛。首次扩大专项包含 1 个旧观察存储断言失败，已保留失败与复测记录，见 `docs/validation/control-plane-backpressure-20261123/README.md`。

2026-11-24 双进程部署与空闲领取优化后统一全量：使用隔离 PostgreSQL 完整运行 `python -m scripts.test_coverage`，Python 2656 通过 / 132 条件跳过 / 0 失败，总体行 92.29%、分支 83.67%，全部目录和关键功能组门槛通过。`backend/app/main.py` 为 97.07% 行 / 84.88% 分支。原有两次未通过证据保留：第一次在旧 503 重试断言处中断；第二次完整运行发现旧领取接口测试仍期待 503，修正后最终轮次通过。132 条跳过保留真实 Docker/systemd/root 等环境条件，不宣称这些场景已由模拟或本机 GPU 演示任务覆盖。完整测试和现场范围见[双进程验证](validation/split-control-api-process-20261123/README.md)。
