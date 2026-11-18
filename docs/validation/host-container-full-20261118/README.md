# 宿主机与容器全量回归（2026-11-18）

测试基线：`1a002cb`。本轮只运行验证，不修改业务实现或部署生产。以下专项通过数有重叠，不能与全量测试简单相加；独立进程与硬件检查未合并进 Python 覆盖率。

## 全量测试

Python 使用项目规定的 `python -m scripts.test_coverage`，首次因两个 `test_bundled_onboarding.py` 同名发生收集冲突，返回 2，未执行测试。首次证据保留于 `coverage-artifacts/host-container-full-20261118/`。设置 `PYTEST_ADDOPTS=--import-mode=importlib` 后重跑，没有排除测试或改变覆盖率分母，报告目录为 `coverage-artifacts/host-container-full-20261118-importlib/`。

全量结果：**1408 passed / 1 failed / 125 skipped，1096.20 秒**，pytest 与覆盖率门禁均未通过。唯一失败为 `test_case_resources.py::test_resource_budget_rejects_oversize_without_partial_jobs`：CPU 申请 10000 时预期 HTTP 400，实际 201。独立重跑该文件仍为 1 passed / 1 failed（5.44 秒）。用例沿用旧资源池上限预期；`unbounded-cpu-memory-defaults/proposal.md` 已明确取消该上限，当前 `job_resources.validate` 也不再据此拒绝申请。此次保留原失败，未修改测试或恢复旧上限；请求接受不等于调度时忽略节点容量。

覆盖率统计见 [完整目录/功能表](coverage-summary.md)：全部 Python **行 89.21% / 分支 79.54%**；调度与资源 96.65% / 86.89%，节点纳管与升级 96.07% / 84.78%，业务执行与结果 91.24% / 81.75%，三项关键功能达到 90% / 80%。唯一覆盖率门槛失败是 **scripts 行 78.51% < 80%**。没有降低门槛或合并未经插桩的专项运行。

125 项跳过中包含本轮在下述独立专项显式开启的 Docker、systemd、编排、PostgreSQL 和迁移用例。仍未开启专用 root 测试账号、破坏性挂载命名空间、独立 onboarding Compose 等环境；不能把独立专项通过数抵消全部跳过，或宣称所有特权场景均完成。

前端 Playwright 全量：108 passed / 10 skipped / 1 failed。失败用例 `login-session.spec.ts:77` 需要 `/src/api.ts`，生产静态服务器没有该源码入口；切换到独立 Vite 开发服务器后，该用例 1 passed。初次失败保留于本地 `frontend/test-results/`，复测证据位于 `coverage-artifacts/host-container-full-20261118-evidence/login-recheck/`。10 项真接口测试缺少专用测试 API/登录配置而跳过，没有将生产平台作为破坏性测试目标。前端没有行/分支覆盖率插桩。

## 真实宿主机与容器

显式开启 `HOST_EXECUTION_TEST=1 GENERIC_DOCKER_TEST=1`，运行 `test_host_backend.py`、`test_host_working_directory.py`、`test_service_recovery.py`、`test_service_readiness_execution.py`、`test_generic_commands.py`：**113 passed / 0 skipped，142.20 秒**。Docker 使用已有 `mini-eval-api:component-upgrade-ui-20261118` 镜像。

覆盖真实 systemd 业务进程、Docker 通用命令矩阵、服务健康检测、启动失败与恢复、工作目录。恢复测试包含模拟 API 边界，不能描述为全生产链路或真实模型评测。

混合编排 **16 passed / 0 skipped，607.56 秒**：`ORCHESTRATION_EXECUTION_TEST=1` 开启 `integration/test_orchestration_execution.py` 和 `integration/test_scheduling_sample.py`。使用隔离 API/数据库、同机两个逻辑节点、真实进程和容器，覆盖 host、docker、host-docker、docker-host、多用例、多批次、共享/独占以及失败/取消。不是跨物理主机网络验收。

## PostgreSQL 并发与迁移

使用仅绑定本机 18432 端口的一次性 PostgreSQL 16.8 容器，迁移到当前 head；没有向生产数据库运行并发或迁移用例。

- 第一组并发：10 passed / 1 failed，31.16 秒，包含调度、目录、镜像、编排和 Agent。
- Agent 用例独立重跑：协议 0、1 均失败，2 failed，10.29 秒。
- 追加批次、报告快照、任务集合、成员关系、监控并发：11 passed，42.61 秒。
- 独立数据库真实迁移脚本：3 passed，15.83 秒。

**已复现缺陷，尚未修复**：`test_agent_configuration_heartbeat_and_claim_are_serialized` 并发发送配置修改、序列 2 的心跳及 claim，心跳返回 200，但最终保存的心跳序列仍为 1。配置 revision=1、desired=stopped 正确，心跳数据被旧 JSON 覆盖。代码检查发现 configure 使用准入 advisory lock，而 heartbeat 使用 Worker 行锁；两者未使用相同的写入互斥。此结果不能判定为并发验收通过。

XML 证据：`coverage-artifacts/host-container-postgres-20261118.xml`、`host-container-agent-race-recheck-20261118.xml`、`host-container-postgres-extra-20261118.xml`、`host-container-migrations-20261118.xml`。

## 本轮 GPU/NPU 容器硬件检查

- 本机 GPU：`mini-eval-runner:dev` 经 Docker 指定一个 GPU UUID，成功加载 `libcuda.so.1`，`cuInit(0)` 成功，设备数为 1。仅验证驱动加载与设备枚举，没有执行 PyTorch 张量运算。
- 29 号 NPU 节点：`mini-eval-npu-service:demo-v1` 首次 PyTorch 检查失败，`ModuleNotFoundError: No module named 'torch'`；这是镜像未提供该依赖，保留失败，不计为 PyTorch 验收通过。
- 同一 NPU 镜像随后通过真实 ACL 检查：仅映射物理 NPU 2 及必要管理设备、只读驱动目录，容器内可见逻辑设备 0；成功加载 `libascendcl.so`，`aclInit`、设备初始化和 1 KiB 设备内存分配/释放均返回 0，输出 `NPU_CONTAINER_ACL_ALLOC_OK devices=1`。

两类硬件容器检查均使用 `--rm --network none --cap-drop ALL --security-opt no-new-privileges`，没有修改镜像。历史宿主机 GPU/NPU 张量验证见 [宿主机专项记录](../host-shell-episode-launch/README.md)，不能算成本轮新执行的模型测试。

本轮临时 PostgreSQL 容器及其数据卷、18081 端口的 Vite 测试服务器已停止/移除。真实启动矩阵使用自身 teardown 清理测试进程与容器。

## 可复现命令

使用仓库 `.venv/bin/python`：

```bash
PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m scripts.test_coverage \
  --output coverage-artifacts/host-container-full-20261118-importlib
HOST_EXECUTION_TEST=1 GENERIC_DOCKER_TEST=1 \
GENERIC_DOCKER_IMAGE=mini-eval-api:component-upgrade-ui-20261118 \
.venv/bin/python -m pytest backend/tests/test_host_backend.py \
  backend/tests/test_host_working_directory.py backend/tests/test_service_recovery.py \
  backend/tests/test_service_readiness_execution.py backend/tests/test_generic_commands.py -q -ra
ORCHESTRATION_EXECUTION_TEST=1 .venv/bin/python -m pytest \
  backend/tests/integration/test_orchestration_execution.py \
  backend/tests/integration/test_scheduling_sample.py -q -ra
```

PostgreSQL 专项分别使用 `TEST_DATABASE_URL` 与 `MIGRATION_DRIVER_TEST_URL`，必须指向一次性服务器。前端在 `frontend` 目录执行 Playwright 全部测试，初次 `EVAL_API_URL=http://127.0.0.1:18000`；需要源码入口的失败用例在 `http://127.0.0.1:18081` Vite 服务器复测。

本轮结论：真实宿主机/容器启动专项通过，但全量质量门禁未通过。待处理 Agent 并发丢心跳缺陷、过时资源预算测试以及 scripts 覆盖率缺口。此次仅提交验证文档与覆盖率摘要，没有业务代码、数据库迁移或生产发布。
