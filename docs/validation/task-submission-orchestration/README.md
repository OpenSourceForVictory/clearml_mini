# 任务提交编排验收

本次按 OpenSpec `task-submission-orchestration` 实施，测试设计见
[59 组验收用例](../../../openspec/changes/task-submission-orchestration/tests.md)。
以下区分纯契约/API 模拟、PostgreSQL 并发、真实业务进程/容器和真实 API 浏览器联调。
本次使用隔离数据库、测试模板和临时工作区，未将生产评测记录当作测试数据。

## 真实执行结果

命令：

```bash
ORCHESTRATION_EXECUTION_TEST=1 \
ORCHESTRATION_TEST_ADDRESS=192.168.8.151 \
ORCHESTRATION_EVIDENCE_DIR=.runtime/orchestration/evidence \
.venv/bin/python -m pytest backend/tests/integration/test_orchestration_execution.py -q
```

8 场景通过：客户端/服务端为宿主机/宿主机、Docker/Docker、宿主机/Docker、Docker/宿主机，
各自覆盖共享与独占服务。每个场景连续提交两个三任务批次，总计 16 个批次、48 个客户端执行、
16 个服务执行。客户端通过平台注入的地址向真实 HTTP 服务发送 case_id、执行 ID 和业务参数，
断言服务回显与平台输入一致；全部完成后服务自动停止，资源清理待确认数量为零。

[执行证据 JSON](execution-evidence.json) 保存每组批次 ID、数量和逐任务回显。
测试使用本机两个隔离**逻辑节点**、真实 systemd 用户服务和真实 Docker 容器。
API 采用隔离 SQLite/TestClient；节点发现和运行环境报告为测试夹具，执行、HTTP 通信、
健康检查、日志、停止和清理由实际 Worker 完成。因此这些结果不等同于跨物理主机网络验收、
NPU 执行验收或真实模型/RoboTwin 仿真评测。

## PostgreSQL 与迁移

使用临时 PostgreSQL 16.8 容器，端口仅绑定 127.0.0.1，执行后删除容器。
迁移 1 项通过；并发测试 6 项通过，包括同键并发提交、8 个调度器竞争、同节点不同服务端口、
已有调度/节点 Agent/目录配置并发回归。

迁移覆盖 `n407c89 → head → n407c89 → head`、旧模板默认值，以及存在活动编排时拒绝降级。
并发编排使用独立随机 schema，不清空已有数据库。请仅在明确的隔离测试数据库配置以下变量：

```bash
ORCHESTRATION_MIGRATION_URL="$TEST_DATABASE_URL" \
.venv/bin/python -m pytest backend/tests/integration/test_orchestration_migration.py -q
.venv/bin/python -m pytest \
  backend/tests/integration/test_orchestration_race.py \
  backend/tests/integration/test_scheduler_concurrency.py \
  backend/tests/integration/test_node_agent_race.py \
  backend/tests/integration/test_storage_race.py -q
```

## 界面与 API

浏览器模拟测试覆盖五步向导、草稿保留、外部/托管/无服务来源、类型化值、预检迟到响应、
幂等重发、模板复制取消/冲突、窄屏、任务与服务关系、日志与控制，以及既有管理功能。
另用 127.0.0.1:18190 的隔离 API/SQLite 执行真实浏览器联调：模板复制编辑发布、新集合与编排
原子提交、资源不足排队、批次关系展示、集合导入/导出/复制/归档及 YAML 校验。业务执行闭环
由上一节单独验证，此浏览器 API 不启动评测任务。

浏览器命令（`frontend/` 内）：

```bash
EVAL_API_URL=http://127.0.0.1:18189 \
ORCHESTRATION_LIVE_TEST_API=http://127.0.0.1:18190 \
SUITE_LIVE_TEST_API=http://127.0.0.1:18190 \
node_modules/.bin/playwright test
```

需要部署 GPU 渲染环境或外部推理服务凭据的历史 live 用例显式跳过，不对生产环境自动提交。

## 验收矩阵对应

| 设计编号 | 主要证据 |
| --- | --- |
| P01–P08 | `test_parameter_contract.py`、`test_task_input.py`、`test_typed_template_flow.py`、`test_command_sdk.py`、真实执行矩阵 |
| P09–P10 | `test_case_resources.py`、`test_gpu_sharing.py`、资源请求字段白名单/能力检查 |
| C01–C06 | `test_template_versions.py`、`test_suites.py`、`test_typed_template_flow.py`、浏览器复制及真实 API 集合用例 |
| A01–A08 | `test_orchestrations.py`、参数/服务模板 API 回归、PostgreSQL 编排竞争、真实 API 向导 |
| S01–S13 | `test_orchestrations.py`、`test_execution.py`、`test_gpu_sharing.py`、PostgreSQL 调度/端口竞争、真实执行矩阵 |
| L01–L08 | `test_orchestrations.py`、`test_services.py`、批次控制/重试/租约测试、真实停止清理 |
| E01–E05 | 普通命令、宿主机后端、SDK、CS 协议测试及 8 场景实际 Worker 执行 |
| U01–U05 | 向导、模板、批次监控与编排关系 Playwright 用例，以及隔离 API 浏览器联调 |
| R01–R04 | 后端全量、迁移、既有浏览器回归、真实双逻辑节点多批次排队 |

## 修复记录与边界

- 实际非 root 容器不能覆盖 root 所有的输入文件：规范化输入改在每次执行的可写工作目录发布，
  argv 占位符和 `MINI_EVAL_INPUT` 指向同一文件；宿主机及本地直接调用保持适配。
- 实际浏览器/API 联调发现托管/无服务模式不能发送空地址列表：前端改为省略该字段。
- 服务预留过期后不能单独重新调度服务：撤销失效依赖，重新联合申请客户端与服务资源。
- 旧界面用例按五步入口更新，SSH 用例等待实际异步请求再断言，未以扩大超时掩盖问题。
- 全量回归期间修改模块曾造成长进程读取旧模块缓存，已重新启动独立的最终全量测试进程。
- 现有每卡/机器并发上限继续生效；本次未移除已有调度策略。NPU 真实执行仍不在本次实现范围。
- 本次没有更新生产 API、Worker 或 Agent；上线需要数据库迁移和支持协议 10 的 Worker。

## 最终结果（2026-11-16）

| 检查 | 结果 |
| --- | --- |
| 后端全量 `pytest backend/tests -q --disable-warnings` | 439 通过，50 个环境依赖项跳过，657.74 秒 |
| PostgreSQL 迁移 | 1 通过，含活动编排降级保护 |
| PostgreSQL 并发回归 | 6 通过，含提交幂等、联合预留和同节点端口竞争 |
| 真实 Worker 执行矩阵 | 8 通过，237.94 秒；16 批次、48 客户端、16 服务，清理归零 |
| Playwright 全部文件（含隔离 API） | 50 通过，5 个部署环境用例跳过，约 1.3 分钟 |
| TypeScript / Vite 构建 | 通过 |
| OpenSpec 严格校验 / git diff --check | 通过 |

后端全量的跳过项需要显式的数据库、挂载、systemd、Docker/GPU 等验收环境。
其中本次相关的 PostgreSQL 和真实执行项已按上面的独立命令执行通过；其余跳过项不计作通过。
混合执行的新增参数化场景在独立的 8 场景执行中验证。Playwright 的 5 个跳过项需要部署凭据、
GPU 渲染或外部推理服务；集合管理与新编排向导的真实 API 联调 3 项均已运行通过。

实现提交：后端 `c8b12d4`，向导与管理界面 `9457eaf`。验收记录与新增组合/迁移/端口测试另行提交。

