# 真机功能、混合上报稳定性与全量回归（2026-11-24）

## 验证范围

基于 `c956faa` 的产品代码，按真机功能 → 100 仿真 Worker 混合上报 → 全量 Python / 前端测试顺序执行。本轮不修改产品代码、不调整部署配置、不重启已纳管节点。

真机使用本机平台现有宿主机 SDK 示例模板；仿真测试使用独立双 API、Nginx 网关与一次性 PostgreSQL 数据库。压测使用当前分类池配置：普通 API 普通/观察/结果为 23/9/7，控制 API 控制池 40；关闭调度线程，因此没有正式部署中普通侧另一个调度池连接。同步线程额度保持默认 40，分类入口闸门关闭，数据库背压开启。

## 真机功能

- 单任务批次：`d0cb8a8b-7117-426e-af6f-3356b78cc176`。
- 同卡三任务批次：`6fbc84be-0b1d-41f0-88a6-603cc865efd3`。
- 节点 `local4090-151`、GPU0；预检 ready，4 个任务均 SUCCEEDED、进程退出码 0、清理确认。三任务批次每任务仅 1 次执行尝试。
- 示例实际由节点 Worker 启动并上报 SDK 任务结果；不执行 GPU 算子或真实模型推理，不代表 NPU、Docker 或模型评测验收。

去敏任务结果见 [真机结果](real-task-results.json)。

## 混合上报配置

每 Worker 独立 HTTP 客户端，运行状态心跳、服务健康、产物登记及领取约每 2 秒一次，Agent 约每 5 秒一次；独立日志生产器每 2 秒产生 1024 字节日志，经序号确认后移除待发记录。100 Worker 目标持续 1800 秒，每 30 秒保留进度，收尾排空 5 秒。

命令使用项目虚拟环境，并将 `LOG_TEST_DATABASE_URL` 指向一次性测试数据库服务器：

```bash
.venv/bin/python -m backend.tests.control_plane_load \
  --grades 100 --seconds 1800 --log-bytes 1024 \
  --ordinary-pool 23 --control-pool 40 --observation-pool 9 --result-pool 7 \
  --independent-logs --drain-seconds 5 --client-per-worker --agent-telemetry \
  --progress-every-30s --split-api --role-aware-pools --no-admission \
  --output .runtime/validation-20261124/load
```

该负载不运行真实调度分配、真实业务进程、完成/清理循环或大量 Web 页面请求；不能据此承诺完整业务 100 Worker 容量。生产平台另每 30 秒只读检查 `/healthz`，这不是 Web 压力测试。

## 保留的启动与配置问题

- 首次启动使用系统 Python，缺少 `psycopg`；随后确认系统 Python 也没有 `psycopg2`。均未开始 API 压测，两个异常日志保留，改用项目 `.venv/bin/python`。
- 第一轮设为收尾排空 30 秒；复核发现脚本在停止心跳后仍等待整个排空窗口，随后检查默认 30 秒租约，可能产生测试本身导致的过期误报。主动停止并保留进度，恢复既有 5 秒收尾配置后重新运行完整 30 分钟。不把提前停止轮记为通过，不改变 API 租约配置。

完整原始证据保留在本机 `.runtime/validation-20261124/`，不提交凭据、API 原始日志或整个运行目录。

## 稳定性结果

100 Worker 完整持续 1800 秒，驱动器退出 0，最终判据通过；原始[汇总](load-summary.json)和[30 秒采样](load-progress.jsonl)保留。

| 通道 | HTTP 200 | HTTP 503 | 成功响应 P99 |
| --- | ---: | ---: | ---: |
| 心跳 | 87,761 | 0 | 69.3 ms |
| 服务健康 | 87,761 | 0 | 49.8 ms |
| Agent 遥测 | 35,844 | 28 | 76.8 ms |
| 日志 | 89,958 | 22 | 85.4 ms |
| 产物登记 | 87,732 | 29 | 52.5 ms |
| 领取 | 87,743 | 18 | 46.8 ms |

共 476,799 个 200、97 个 503，503 均为 `connection_pool`，没有调度锁 503 或传输异常。普通通道仍存在短暂连接池拒绝，不能称为零失败或容量问题已完全消失。

日志生成/确认 89,958/89,958，最终待发 0；过程中每 Worker 最多待发 2 条、最老待发约 2.19 秒。30 秒采样点待发均为 0，不代表采样之间从未排队。最终 SQL 租约过期 0、遥测过期 0；最长心跳间隔指标（包含收尾时末次心跳年龄）4.05 秒，运行阶段采样记录的最差间隔 2.21 秒。末次 Agent 成功距 Worker 循环结束约 7.00 秒。这是本次特定负载通过，不代表任意日志体积、真实业务、Web 压力或 200 Worker 的容量保证。

## 全量回归

Python 使用项目虚拟环境与 `PYTEST_ADDOPTS=--import-mode=importlib`，运行 `python -m scripts.test_coverage --output .runtime/validation-20261124/coverage`，并提供隔离的 `TEST_DATABASE_URL`、`LOG_TEST_DATABASE_URL`、`MIGRATION_DRIVER_TEST_URL`。全量前将一次性测试库迁移到 head，完成后删除该库。

**2656 passed、132 skipped、0 failed、11 warnings，2072.57 秒（34 分 32 秒）；pytest 退出 0，覆盖率门槛通过。** Python 总计行 92.29%、分支 83.68%；所有目录和关键功能组均达标，逐组数据见 [覆盖率报告](python-coverage.md)。前端与未插桩的真机子进程不在此覆盖率内。

[跳过原因明细](python-skips.json)保留全部 132 项：包括真实 Docker/systemd 显式开关、指定测试镜像、特权挂载、上一版 API 对照与专门迁移数据库条件。启用了本轮 PostgreSQL 集成和迁移驱动测试，并不代表所有额外迁移矩阵都已启用；不能把 4 个真机示例任务当作这些跳过场景的替代。

前端首轮发现并修正测试定位冲突后，向导专项 **27 passed**（12.8 秒），完整前端复测 **248 passed、10 skipped、0 failed**（4.0 分钟），退出 0。`npm run build` 通过；仍有既有 bundle 超过 500 kB 的体积提示。10 项跳过涉及显式启用的真接口/真实业务测试，不算作通过；前端测试没有统一行/分支覆盖率。

本轮只修改回归测试的控件定位与断言，以及验证文档、覆盖率说明、Skill 索引和 OpenSpec 任务记录；没有产品代码、数据库结构、Worker/Runner 版本或部署变更。已检查相关用户指南，无用户流程变化，不改写无关操作指南。

前端初始串行启动器未找到默认 PATH 中的 npm，未执行任何前端用例；随后显式使用 `/tmp/clearml-node-v22/bin` 中的 Node 22.12.0 / npm 10.9.0 启动完整 Playwright 与构建。该启动环境错误不计作前端用例失败，记录保留在本机运行说明。

前端首轮为 247 通过、10 跳过、1 失败（4.0 分钟）。失败 `wizard-clarity.spec.ts` 的非法服务环境变量用例采用模糊标签 `服务 1 模板`，同时匹配模板选择框与新增的模板说明区域，触发 strict mode violation。页面实际已经显示禁止覆盖的错误并保留原 JSON。修正为精确的 combobox 定位，并额外断言说明可见、错误提示、选中模板保留、资源步骤不可见、未发送预检；不弱化原业务断言或修改产品行为。首轮日志及页面错误快照保留在 `.runtime/validation-20261124/`。

## 环境与证据收尾

生产平台 `/healthz` 在压测、Python 全量及首轮前端期间共采样 140 次，全部 200；完整前端复测后再次检查正常。该端点不访问数据库，不能据此推断 Web 查询在高负载下的容量。独立压测 API/网关和本轮临时数据库均由驱动器清理，已有测试 PostgreSQL 容器保留供后续专项复用。
