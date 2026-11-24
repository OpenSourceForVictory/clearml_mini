# 全项目 Python 覆盖率补测（2026-11-24）

生产源码基于 `a7f8366`，此次只新增测试及同步文档，没有修改生产逻辑、统计分母或门槛，也未部署。六个目录及全部功能分组通过现有门槛；不是 100% 代码覆盖，也不是全部特权/硬件专项验收。

## 结果

- 全量统一命令：2663 passed、132 skipped、0 failed，10 warnings，2106.78 秒。迁移和覆盖率命令退出码均为 0。
- 新增 `test_release_recovery_boundaries.py` 16 个用例；与原有发布回归一起执行 66 passed、0 skipped、0 failed，1 warning，12.46 秒。其余 50 个用例与全量重叠，不能重复计数；本次共 2679 个不同用例通过。
- 当前全量基线已经达标：92.32% 行 / 83.69% 分支。合并本次同一生产源码的补测后：**92.57% 行 / 83.87% 分支**；scripts 从 80.82% / 78.20% 提升至 **82.12% / 79.25%**。
- 目录门槛 80% / 70%，三个关键功能门槛 90% / 80%；未降低门槛、排除源码或合并旧代码版本的数据。

逐目录与功能计数见 [最终覆盖率](python-coverage.md)，未合并补测的全量结果见 [全量基线](full-suite-coverage.md)，执行计数见 [测试结果](test-results.json)。

## 新增用例的断言

- 真实临时文件：备份内容、权限、SHA256 清单、空备份拒绝及清理。
- Docker 命令故障注入：隔离恢复网络、就绪重试/超时、恢复与迁移顺序、历史执行数保护、容器创建/恢复失败清理、命令异常脱敏。Compose 命令保留 LAN overlay 和绝对路径。
- 本机临时 HTTP 服务：JSON 请求、POST、认证头、幂等键、401、非 JSON 响应及连接断开；非法平台 URL 拒绝。

以上模拟 Docker 命令不会启动或停止生产容器，不能作为真实发布恢复验收。

## 可重复性与边界

全量使用 `.venv/bin/python -m scripts.test_coverage --output .runtime/coverage-complete-20261124/coverage`，设置 `PYTEST_ADDOPTS=--import-mode=importlib`。隔离 PostgreSQL 容器中创建随机测试库并迁移，测试结束删除；`TEST_DATABASE_URL`、`LOG_TEST_DATABASE_URL`、`MIGRATION_DRIVER_TEST_URL` 仅指向隔离环境，凭据不进入记录。

全量开始收集后新增的用例另行测量，使用同一 `backend/tests/project.coveragerc`、分支模式与生产源码。覆盖数据通过 `coverage combine --keep` 合并后，用 `python -m scripts.coverage_report` 和原门槛生成最终报告；全量原始报告不覆盖。未合并此前专项报告或其他版本的数据。

本地原始数据位于 `.runtime/coverage-complete-20261124/`：全量 `coverage/`、补测 `supplement.coverage` / `supplement-tests.xml`、合并 `combined/`。该目录未提交。

132 项跳过原因逐条保存在 [跳过明细](skips.json)：主要需要显式 Docker/systemd/特权挂载开关、指定测试镜像或另外的专用迁移数据库。虽然本次已启用部分 PostgreSQL 集成及迁移驱动器，仍不能声称这些不同开关控制的专项都已执行。没有以覆盖率为由在生产节点启用破坏性专项。

前端未统一插桩，未合并的独立业务进程也不计入本报告；本次未重跑浏览器或真机模型评测。警告为测试依赖/框架警告，测试报告中保留。未出现失败用例；初次 10、14 个新增测试通过后扩展为最终 16 个，并执行上述 66 个发布回归。
