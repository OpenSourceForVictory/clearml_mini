# 全量测试与节点核对（2026-11-19）

## 运行边界

Python 使用 `PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/host-recovery-full-20261119`，项目统一分母与门槛，不排除未测代码。运行时 Python 实现对应 9df8200；本轮额外修正前端和 Agent 测试夹具，未改变产品实现。未连接生产数据库执行测试。

前端类型检查及生产构建通过（存在 bundle 大于 500 kB 提示）。Playwright 使用独立本地 Vite、模拟 API，清空继承的测试账号及 live API 配置；真实部署用例按原规则跳过。浏览器未插桩，不计入 Python 覆盖率。

## 前端结果与修正

第一次误用静态 dist 服务，动态加载 /src/api.ts 的用例失败，已中止该轮（frontend-static-harness-aborted.log）；这是测试环境错误，不是页面缺陷。

改用 Vite 的完整结果：136 passed、2 failed、10 skipped，见 frontend-before.log。两个 cs-tabs 用例的旧夹具缺少 execution-limits 响应，页面正确提示“请先读取平台执行时限上限”并阻止继续，随后定位输入框超时；页面快照见 collection-before.md。仅补齐夹具的有效上限/默认值，不绕过页面校验，3 个相关用例复测全部通过（frontend-recheck.log）。没有再跑第二轮前端全量，因此保留首轮失败计数，不声称一轮全绿。

## Python

首轮全量 **1628 passed、133 skipped、1 failed**，耗时 1122.61 秒，见 python-full.log；收集 1762 项。133 项跳过是该默认命令的实际结果，未将其算作通过，具体明细保留在 coverage-artifacts/host-recovery-full-20261119/tests.xml。

Agent HTTP 集成用例失败于最终读取模拟 systemctl 状态 JSON（test_agent_integration.py 原第 60 行），JSONDecodeError 表明读到了被截断、尚未写完的空文件。模拟命令原先连只读 inspect 都写文件；后台 Agent 持续调用 inspect，与断言读状态存在窗口。隔离复测虽通过（agent-recheck.log），仍保留初始失败。修正仅对 start/stop 修改模拟状态，并用同目录临时文件 replace 原子替换，不修改 Agent 产品逻辑、不删除断言或增加超时。修正后 test_agent_integration.py 与 test_agent_modules.py 共 **13 passed**（agent-fixed.log）。没有重跑第二轮 Python 全量。

覆盖率见 coverage.md：总行 **89.47%**、总分支 **79.93%**；调度与资源 96.64%/86.81%，节点纳管与升级 95.61%/83.52%，业务执行与结果 91.77%/82.74%，关键功能组达到 90%/80%。scripts 行 **78.38%** 低于目录 80% 门槛，整体覆盖门槛未通过。前端与未插桩独立进程未计入；未通过降低门槛、排除业务文件或合并旧实现数据规避。报告为首次完整运行，不能称本轮全部通过。

## 节点观察

见 node-observation.md。29 心跳持续更新，8 张卡上报 0.20.5；SSH 超时，未升级节点。历史 missing unit 记录不等于已确认用户最新现场根因。


## 补充真实数据库验证与覆盖门槛

后续使用专为本次创建、仅监听本机随机端口的一次性 PostgreSQL 16.8 容器，启用 test_migration_driver_scripts.py 的 3 项既有用例，结果 **3 passed，10.32 秒**（real-migrations.log）。每项创建独立数据库，真实执行集合、资源池归属、镜像运行环境的历史数据回填、受保护降级、降级后重新升级；无模拟迁移命令。完成后已移除本次临时数据库容器，未触碰生产数据库。

收集命令使用与全量相同的 backend/tests/project.coveragerc，MIGRATION_DRIVER_TEST_URL 指向临时数据库；coverage.py 在同一进程通过 runpy 记录迁移检查脚本。Alembic 子进程未插桩，不宣称其独立进程覆盖率。

产品代码未改变，合并原始全量 .coverage 与本次独立迁移 .coverage；保留原始报告，扩展报告在 coverage-artifacts/host-recovery-full-20261119-extended/，摘要见 coverage-extended.md。同样的 scripts.coverage_report 和门槛检查返回 0：总行 **90.04%**、总分支 **79.93%**；scripts **80.81% / 78.20%**，所有目录及关键功能组门槛通过。未降低阈值、未排除文件、未合并其他产品版本。

这是全量加补充用例的累计覆盖证据，不是第二轮完整测试。原始全量的 1 失败/133 跳过保持不变；其中 3 个迁移跳过项已在补充运行中验证通过。夹具修正后的相关复测结果见上文，不用覆盖率通过替代测试结果。
