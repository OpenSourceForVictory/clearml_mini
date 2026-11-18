# 全量测试与节点核对（2026-11-19）

## 运行边界

Python 使用 `PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/host-recovery-full-20261119`，项目统一分母与门槛，不排除未测代码。运行时 Python 实现对应 9df8200；本轮仅额外修正前端测试夹具。未连接生产数据库执行测试。

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
