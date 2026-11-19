# 业务执行与日志功能组补测（2026-11-19）

补测发现实际缺陷：日志轮转先删旧分片后提交索引，索引提交失败会留下指向已删除文件的旧索引。失败证据 rotation-bug-before-fix.txt。Worker/Runner 0.21.7 使用持久 prune.json 清理意图，先提交 manifest，后删除已不被索引引用的分片；写入或 Worker 历史同步时恢复未完成清理。无新增数据库迁移，未部署平台或升级现场节点。

## 新增场景

| 范围 | 测试及边界 |
| --- | --- |
| 宿主机回合控制 | 实际子进程；旧日志/新采集、stop/continue、成功/业务失败/缺失/重复/非法结果/非零退出，检查参数、seed、退出码、事件去重与最终汇总 |
| 存储边界 | 身份/代次、硬链接、路径穿越、分片被替换或截断、重放序号、非法预算、索引大小、短写后重试 |
| 崩溃恢复 | fork 的真实写进程通过 os._exit 退出，分别位于索引提交前、提交后及旧文件删除后；新 Worker 日志服务恢复后文件与序号一致且能继续写入 |
| 清理安全 | 篡改清理意图、任意路径、错误 scope/执行/代次、被替换 inode、硬链接和符号链接均拒绝删除 |
| 并发与容量 | 四个独立进程共享一个执行预算，各写 32 条约 6 KiB 记录，检查实际文件大小、序号和 scope 归属；约 25 MiB 连续输出面对阻塞 sink，队列和 stderr 尾部有界 |
| 读取与清理 | 实际 flock，读页持锁期间清理不能进入；读页完成释放后清理成功，下一次读取为文件不存在 |
| 大历史索引 | 构造已有 10000 回合索引及真实末回合文件，一次 Worker 同步最多 16 项、可读取末回合；10004 scope 拒绝。不是运行一万次模型或业务进程 |
| 新旧能力协商 | 无能力、仅旧日志、仅新标志、完整能力四种组合，只有完整组合启用新采集 |
| API 离线积压 | 17 个测试账号、128 个待处理请求，超额 429；TTL 过期释放，连接归还，心跳与取消可达 |
| PostgreSQL 小池 | 临时独立数据库，pool_size=2/max_overflow=0，串行及 8 线程并发提交请求；无连接超时、请求完成后 checkedout=0 |

新增文件：test_execution_group_gaps.py、test_process_log_concurrency.py、test_process_log_load.py。前两份 45 + 15 项；负载/兼容性 7 项（其中两项独立 PostgreSQL）。旧日志、SDK 和 Worker 相关测试仍一同参与功能组回归。每次 PostgreSQL 测试由已有 pg fixture 创建、销毁独立数据库，不清理用户数据库；Docker PostgreSQL 仅为本次专用容器。

## 负载证据的含义

两连接池测试，串行 128 次请求的单次最大耗时 0.0185 秒、累加耗时 1.3929 秒；8 线程并发时单次最大 0.0987 秒、累加耗时 10.9430 秒。并发累加耗时不是墙钟吞吐时间。测试冻结日志请求时钟，以精确验证 TTL 到期，真实耗时另用 monotonic 记录。完整输出见 postgres-two-connections.txt。不能据此承诺生产延迟，也不能替代真实长期多用户负载。

## 失败与未完成边界

- 轮转提交失败是实现缺陷，已通过新顺序与恢复修正。
- 首轮存储夹具误把 RecordEncoder.feed 生成器当列表，16 项未进入目标断言；修正为 next 后通过，保留 test-fixture-first-failure.txt。
- 队列测试最初重复计算 inflight，bytes 本来已包含它；改为同时校验 bytes 上限和 queue+inflight=bytes，保留 queue-test-first-failure.txt，没有放宽容量。
- API 取消操作返回 202 Accepted，首轮测试误期待 200；修正断言。该失败输出包含测试认证头，故不提交原始全文。
- 为修复已复现的轮转问题，中止了旧代码的首轮广泛基线；不把中止结果当最终验收，最终重新运行固定功能组分母的关联测试。
- 仍未执行真实节点升级/回滚、断电或任意 NFS 故障、长时间生产负载、大文件下载分片回退及配置 revision 界面验收。没有把模拟能力协商和进程退出测试称为真实发布验收。

本次同步执行日志、升级说明、界面指南、覆盖率说明、项目 Skill 及 OpenSpec；原复合任务未因部分测试完成而全部勾选。

## 最终覆盖率与回归

使用 `python -m scripts.test_coverage --output coverage-artifacts/execution-group-final`，通过 PYTEST_ADDOPTS 选择 Worker/Runner、宿主机、SDK、回合、参数、挂载、执行日志与结果相关测试；完整选择表达式见 command.txt。保留全部源码统计分母，不合并旧报告或独立子进程数据。

870 passed、40 skipped、1190 deselected，382.11 秒。业务执行与结果功能组 **38 文件、行 91.21%、分支 83.13%**，达到 90%/80%。runner 目录 88.76%/79.53%，达到目录 80%/70%。整个仓库此次只有关联测试，其他目录/功能组仍未达标，报告命令因此非零退出；不能称作全量测试或全项目覆盖率达标。完整门槛失败保留在 summary.md 和 execution-group-tests.txt，跳过原因见 skips.txt。

关键日志模块（行/分支）：process_log_store 98.27%/93.59%，process_log_run 100%/97.37%，process_log_capture 99%/96.15%，worker/process_logs 100%/98.39%。前端只有 TypeScript 检查，不宣称前端覆盖率；实际业务子进程未合并覆盖率。

功能组运行过程中仅对负载测试做了独立数据库夹具及并发参数化调整，生产代码保持不变。最终版本负载测试另行运行，覆盖率报告未合并该独立运行。40 项跳过含显式开启的 Docker/systemd 真机矩阵等，不等同于已经验证真实节点升级。

最终负载文件独立回归：7 passed，36.50 秒，见 load-final.txt。Ruff、TypeScript 检查及 OpenSpec strict 校验通过。
