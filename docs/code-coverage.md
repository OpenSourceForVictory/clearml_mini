# 代码覆盖率规范

本项目按目录和关键功能分别检查，禁止用少数模块的高覆盖率代表全项目。覆盖率衡量执行到的代码，不替代业务断言、并发测试或真机验收。

## 强制门槛

| 范围 | 行覆盖率 | 分支覆盖率 |
| --- | ---: | ---: |
| 每个 Python 目录 | ≥80% | ≥70% |
| 调度与资源、节点纳管与升级、业务执行与结果 | ≥90% | ≥80% |
| 其他功能分组 | ≥80% | ≥70% |

配置在 `backend/tests/coverage-floors.json`，CI 与本地使用相同规则。任何测试失败或任一门槛未达到均返回非零；既有代码未达标也不会自动豁免。新增功能必须随代码提供正常、边界和失败路径测试。不得通过排除未测文件、mock 掉需要验证的行为或降低门槛凑数字；调整统计范围/门槛需有明确需求和评审说明。

## 运行

在独立测试环境运行，禁止指向生产数据库。Python 3.11+，完整命令：

```bash
pip install -r backend/requirements.txt -r backend/tests/requirements-coverage.txt
python -m scripts.test_coverage
```

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
