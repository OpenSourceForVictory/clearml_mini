# 调度预约窗口专项验证

## 触发条件与修复

资源预约使用本轮较早的时间计算期限，但事务还需完成其他任务的分配、服务补齐和写入。默认窗口 30 秒；模拟预约后调度尾部耗时 31 秒，旧实现提交时剩余 -1 秒。未访问生产数据库，复现使用独立内存/临时 SQLite 数据库与虚拟时钟。

修复仅登记本轮新预约、服务就绪后新开启的领取窗口，在主要写入 flush 后、提交前刷新对应期限。普通窗口仍取 RESERVATION_SECONDS；托管等待预算保持服务启动时限加 STARTUP_TIMEOUT_SECONDS。旧预约不自动续期，预约决策时间、资源分配、执行身份、运行租约、业务时限和清理规则不变。

最终期限写入和 commit 本身仍消耗时间；本修复不承诺任意数据库阻塞后都有完整窗口，不刷新资源遥测、不跳过领取复查。无需数据库迁移或 Worker 更新；2026-11-24 已完成本机双 API 发布，见 [发布验证](deployment.md)。

## 失败证据

修改前新增的首批 12 项用例：8 failed、4 passed，38.62 秒，见 [原始失败输出](before.log)。失败集中在非零调度尾部耗时的新预约、过期后重新预约、回滚后新轮次、服务配对/补实例及服务就绪窗口；原有预约不续期和显式固定时间控制通过。

修复后首轮 22 passed、2 warnings，70.70 秒（首批 12 项加已有调度用例 10 项）。随后增加失效身份保护、辅助函数独立使用及真实 HTTP claim 路由测试，并扩大关联回归。

## 复现命令与统计口径

最终专项合计 **237 passed，0 failed，0 skipped**（不重复计入前面的 22 项首轮回归）。新增 17 个参数化实例：

| 运行 | 结果 | 时间 | 证据 |
| --- | --- | --- | --- |
| 调度/编排/领取/暂停/共享/就绪 | 102 passed，2 warnings | 315.10s | [日志](related.log) |
| PostgreSQL 执行锁边界及调度循环失败恢复 | 13 passed，1 warning | 22.57s | [日志](locks.log) |
| 连接预算/资源上报/资源契约 | 122 passed，1 warning | 62.72s | [日志](resources.log) |

警告来自现有 Starlette 弃用和 Pydantic 字段声明。原始 JUnit、完整覆盖数据位于 `.runtime/reservation-window-20261124/`，不提交整个运行目录。

| 当前源码统计范围 | 行覆盖率 | 分支覆盖率 |
| --- | ---: | ---: |
| scheduling.py | 94.52% | 84.93% |
| orchestration_scheduler.py | 94.61% | 81.36% |
| 调度与资源（完整五模块功能组） | 94.23% | 82.72% |

该功能组达到 90%/80% 门槛；**全项目门禁未通过**：仅运行专项，其他目录/功能仍有 24 条门槛违规，不宣称全项目达标。详细数据见 [results.json](results.json) 和 [统一口径门槛报告](coverage-summary.md)。新增窗口辅助函数的行及分支均覆盖。

```bash
COVERAGE_FILE=.runtime/reservation-window-20261124/.coverage \
  .venv/bin/python -m coverage run --rcfile=backend/tests/project.coveragerc -m pytest \
  backend/tests/test_reservation_windows.py backend/tests/test_scheduling.py \
  backend/tests/test_orchestrations.py backend/tests/test_desired_service_instances.py \
  backend/tests/test_batch_controls.py backend/tests/test_execution.py \
  backend/tests/test_gpu_sharing.py backend/tests/test_accelerator_resources.py \
  backend/tests/test_service_readiness.py -q --tb=short \
  --junitxml=.runtime/reservation-window-20261124/tests.xml
```

使用 `scripts.test_coverage` 同一 project.coveragerc、coverage JSON 及 scripts.coverage_report 分母/门槛，仅选择关联测试，不是全量测试。未合并以前全量数据；前端、独立 Worker/容器及真机执行未计入本次统计。不能由单文件覆盖率或专项通过数宣称全项目门槛通过。

后两组在相同 `COVERAGE_FILE`、相同配置和未变化的生产源码下使用 `coverage run --append`：分别选择 `test_execution_lock_scope.py` 加 `test_reservation_windows.py::test_scheduler_loop_recovers_after_failed_reservation_commit`，以及 `test_db_budget.py test_resource_reporting.py test_resource_contract.py`。需通过 `LOG_TEST_DATABASE_URL` 提供一次性测试服务器连接，测试自行建库/清理，不可指定生产服务器。最后使用 `coverage json --rcfile=backend/tests/project.coveragerc` 导出，并以 `python -m scripts.coverage_report --input <coverage.json> --output <report目录>` 生成统一口径报告；本次该报告因其他范围未达标返回 1，失败状态保留。

## 验证边界

虚拟时钟覆盖 0、29、30、31、120 秒计算尾部；事务提交失败使用故障注入并验证数据库回滚；领取使用 FastAPI TestClient 真实路由，独立提高测试节点在线期限以隔离另一项超时。本次不更改锁顺序或运行协议。

另在既有一次性测试 PostgreSQL 容器 `mini-eval-final-tests-pg-20261120` 上运行锁边界回归。测试为每例新建随机数据库并在结束删除，不使用平台数据库；覆盖续租与全局锁隔离、领取锁忙、超时复查及取消/终态保持。这不是完整调度吞吐或长期稳定性测试，也不是真实 Worker 或 GPU/NPU 业务验证。
