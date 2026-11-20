# 最终补全回归记录

第一轮冻结源码9fb9943：2278通过、6失败、99跳过，耗时1840.30秒；覆盖率行91.91%、分支83.00%，目录及关键组门槛通过，但测试门禁失败。失败均来自直接适配器旧测试缺少回合context，修正提交3f1938b；真实子进程6项复测通过。保留原失败，不将覆盖率达标写成测试通过。

已完成新版目录功能及测试提交ec458c7后，正在统一重新执行全部Python；不同源码的覆盖数据不合并。第一轮报告见first-coverage.md、first-result.json。前端初轮226通过/10跳过；加入自定义目录后228通过/10跳过，未插桩不计Python覆盖率。


中间重跑记录：第一轮补全运行到约88%时工具会话返回143，未形成XML/coverage，不能算通过。改用脱离工具会话的后台进程后，在审计中复现SSH执行器等待占用连接池的新问题；为改用修正后的源码，主动对这轮pytest发送SIGINT，保留部分XML和覆盖率（interrupted-result.json / interrupted-coverage.md），不合并进最终统计。中断堆栈不是业务回归失败，也不将部分覆盖率作为最终门槛结论。

最终源码c659a63已冻结，使用独立后台进程重新运行官方scripts.test_coverage，包含新增日志/产物/SSH隔离测试；报告完成后补录。前端最终全套241通过、10条件跳过（3.7分钟）。日志/产物关联184通过，SSH修复关联55通过，均无跳过；新增用例不靠降低门槛或排除业务文件。

## 最终冻结版本结果

冻结提交 `c659a63` 的官方全量命令已完成：**2454 通过、0 失败、99 条件跳过**，耗时 **1843.398 秒**。跳过原因、测试明细和原始 XML 保存在独立回归输出目录；跳过项没有被删除，也没有降低门槛。

- 全部 Python：行 **91.99%**，分支 **83.15%**。
- `backend/app`：96.00% / 84.22%；`worker`：92.27% / 82.82%；`node_agent`：91.67% / 84.29%；`runner`：95.01% / 86.69%；`onboarding`：97.49% / 84.15%；`scripts`：80.81% / 78.20%。
- 关键功能组：调度与资源 97.65% / 88.54%；节点纳管与升级 95.67% / 83.65%；业务执行与结果 96.29% / 89.33%。
- 本轮前端全套：241 通过、10 条件跳过；前端未插桩，不纳入 Python 覆盖率。

原始报告位于 `.runtime/final-followup-20261120/verified/`（`tests.xml`、`test-exit.json`、`summary.md`、`summary.json`、`coverage.xml`、`coverage.json`）。该目录是本机验证产物，不提交到 Git；本文件记录可复核的汇总结果。

## 全量之后的真实 Docker 补跑

复核跳过原因后，在本机 Docker 29.4 上单独启用了全量期间未开启的 `GENERIC_DOCKER_TEST=1`，运行 `backend/tests/test_generic_commands.py::test_docker_real_command_matrix`。使用本地隔离镜像 `mini-eval-api:root-managed-20261117`（image ID `sha256:16f8bca74599ba7ee7db6c539cc8e865d96a9f2b3354ebbac765ddd96603f570`）；每个容器使用临时目录、禁用网络、移除 capabilities，并限制为 1 CPU / 256 MiB。**24 项全部通过，11.45 秒**。这是一项独立补跑证据，不回写全量 `tests.xml` 的 99 跳过数，也不代表 systemd、特权挂载、迁移数据库或多物理节点用例均已补跑。

## 条件跳过项补跑（2026-11-20）

之后通过当前用户已运行的 systemd user manager（显式设置 D-Bus 地址）、现有隔离 PostgreSQL 测试容器及临时 `mini-eval-onboarding-test` Compose 项目补齐了更多条件。每项均独立运行，不合并进上述全量覆盖率或原始 XML：

- 宿主机、容器、服务健康检查、恢复、工作目录和通用命令矩阵：**113 passed / 0 skipped**，129.33 秒。
- 两个逻辑节点的混合调度、多批次和样例执行：**16 passed / 0 skipped**，568.59 秒。
- 节点目录挂载、只读边界、模板容器，以及隔离特权挂载命名空间：**18 passed / 0 skipped**，14.23 秒。
- 独立 onboarding Compose 的重启/故障恢复验收：**1 passed**。SSH 仅连接本机 Docker 网桥上的主机密钥探测端点；测试没有确认指纹、认证或安装。测试结束后已删除该 Compose 项目、容器、网络和卷。
- 一次性 PostgreSQL 上的 race 与迁移专项：**28 passed / 1 failed**，94.13 秒；Retention 迁移单独补跑 **1 passed**。唯一失败是 `test_endpoint_migration.py:31` 断言升级后的版本应为 `i952d34`，但当前 Alembic head 是 `c952f34`，数据库实际返回 `c952f34`。迁移及前序数据断言已通过，失败发生在版本号断言；原始 JUnit 与日志保存在本机 `.runtime/final-followup-20261120/skip-condition-recheck/`，未提交。

剩余 5 项 `MANAGED_HOST_TEST` 用例没有在本机开启：它们必须以 root 运行真实 systemd system units；当前测试会话是普通用户，非交互 sudo 不可用。没有把容器模拟冒充成 root 宿主机验收。全量 XML 仍记录原来的 99 条件跳过；补跑数据不修改原覆盖率统计。以上测试期间未改业务代码；唯一已发现的失败是迁移测试对旧 Alembic revision 的断言。
