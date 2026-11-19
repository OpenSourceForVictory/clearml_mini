# 子进程采集与 Worker 日志接线故障验证（2026-11-19）

本次仅补测试与文档，没有修改生产执行逻辑、SDK、数据库或节点版本，没有部署/升级节点。沿用 OpenSpec process-scoped-execution-logs，不改变待完成的压力/恢复/发布要求。

## 新增测试

`backend/tests/test_process_log_failures.py` 共 37 项通过。覆盖：

- 采集未协商拒绝、日志根丢失降级、写盘失败、关闭失败、业务启动失败，保留实际退出码。
- 真实子进程输出超长行、无换行尾部、stdout/stderr，回调抛错后回收仍在运行的子进程；慢 sink 不无限阻塞业务退出，记录 incomplete。
- Worker 重建日志服务后重用私有登记；失联执行的 task 关闭、业务 scope 标记 incomplete；分批同步 19 个 scope，已确认目录不重复提交。
- 根 inode、代次、执行身份和 scope 身份被替换时拒绝读取/登记；缺失目录与仅缺索引区别处理；跨槽位不读取文件。
- 实际 flock 清理锁冲突返回忙，释放后恢复；邮箱分别回复 busy/cleaned/failed/ready，不泄露注入错误详情。
- API 404 清除私有登记，503 保留并延后；发送失败保留未确认目录供下次服务恢复同步；后台线程异常后仍可关闭。

测试使用实际子进程/文件/锁。磁盘慢写、写入失败和 API 断线用可控故障注入，不宣称断电恢复、真实磁盘耗尽或高并发压力验收。

## 覆盖率与命令

关联统计命令：

```bash
PROCESS_LOG_TEST_IMAGE=mini-eval-runner:dev \
PYTEST_ADDOPTS='--import-mode=importlib -k "process_log or log_capture"' \
.venv/bin/python -m scripts.test_coverage --output coverage-artifacts/log-failure-coverage
```

159 passed、1 skipped（原因见 skips.txt）。此轮包含首批 35 项故障测试；之后增加慢 sink 和真实 API 失败恢复两项，单独跑整个故障文件 37 passed。两轮有重叠，不相加为独立测试总数。生产代码在两轮之间未改变，后两项未合并到这份覆盖率统计。

| 模块 | 行覆盖率 | 分支覆盖率 |
| --- | ---: | ---: |
| runner/process_log_run.py | 100% | 97.37% |
| worker/process_logs.py | 100% | 98.39% |

这两个文件均高于关键功能的 90%/80% 数值要求，但项目的实际门槛按目录和功能组执行；业务执行功能组及其他目录仍未达标，完整失败门槛保留于 regression-and-thresholds.txt。没有降低门槛、删掉未测业务代码或只统计两个模块。module-coverage.json 保存原报告的逐行和分支证据。

采集器和 Worker 服务在受 coverage 监测的测试进程内执行；业务子进程和 Docker 内部的 Python、前端没有合并到 Python 覆盖率，不宣称已统计 SDK 内部全部路径。

## 失败证据与边界

首轮 1 failed / 34 passed：超长行测试要求 stderr 必须只有业务文本，但慢文件系统可能触发采集器允许的“process log close incomplete”诊断。修正为要求业务文本完整保留，后缀只能为空或该诊断；没有放宽退出码或丢弃任意输出的断言。原失败输出见 first-failure.txt，最终 37 项输出见 fault-tests.txt。

完整崩溃恢复、整机配额通道、生产发布和大规模压力仍属于原设计未完成项，本次没有通过单文件覆盖率替代这些验收。同步了 code-coverage、OpenSpec 验证/任务记录和项目 Skill；用户操作行为未改变，无需更改界面操作指南。
