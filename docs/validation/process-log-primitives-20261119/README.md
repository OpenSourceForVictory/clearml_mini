# 独立日志基础模块阶段验证

日期：2026-11-19。配额通道继续待决，本阶段没有生产采集接入、部署或节点升级。

## 实现和边界

- RecordEncoder 按来源增量处理 UTF-8，生成带序号、采集时间、来源、文本及 gap 的有界 JSONL；不等待换行。
- BoundedWriter 将文件写入与生产者解耦，包含 in-flight 记录的队列有界，保留有限 stderr 尾部；磁盘错误仅记录 write_failed，慢写退出有限等待，过载计入缺口。
- Worker 读取器使用登记根目录身份、逐级 no-follow 目录句柄和可信分片索引；支持有限分页、反向、来源过滤与缺口。不得直接以业务 manifest 为授权来源。
- 读取保留日志时间和缺口字段，全部淘汰可返回空页缺口；队列丢失造成序号跳跃标记缺口，重复/倒退序号与索引不符拒绝。这些在草稿审查中修正，测试均为修正后运行，没有声称预先获得失败测试。
- 尚未完成分片写入/原子索引恢复、生产管道排空接线、配额、清理锁协调、Worker 邮箱接入或日志抽屉。OpenSpec 整项验收保持未勾选。

## 验证方式

测试使用真实临时文件和本地 Python 子进程；ENOSPC/EROFS/EACCES 由 sink 故障注入模拟，慢写由 Event 控制；没有真的填满磁盘或改生产权限。子进程大量输出且退出 7，断言日志故障不改变其实际退出码。不是 GPU/NPU 模型测试，也未验证宿主机/容器生产采集链路。

专项覆盖分块 UTF-8、非法编码、超长无换行、队列顺序、stderr 尾部、磁盘故障、积压/丢弃、关闭等待、游标执行/代次隔离、正反分页、过滤空页、淘汰缺口、穿越、符号/硬链接、FIFO、替换、截断、读取中删除、异常 JSONL/索引及页字节上限。

统一口径命令：

```bash
PYTEST_ADDOPTS='--import-mode=importlib -k "process_log or log_capture or execution_logs_v2"' \
  .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/process-log-primitives-final
```

分母保持全部 Python，不降低门槛或排除业务代码。前端与未插桩子进程不计入覆盖率。首次关联回归 52 通过、1 跳过，随后新增序号缺口与索引范围测试，再从空覆盖数据运行最终回归；没有跨源码版本合并数据。测试通过不表示项目覆盖率门槛已通过。

同步 docs/execution-logs.md、项目 Skill 参考索引和 OpenSpec 阶段记录。本次没有新增迁移；此前 z629c01 基础迁移不等于独立日志功能已经发布。

## 最终结果

54 passed、1 skipped、1861 deselected，43.43 秒；跳过 `test_process_log_migration_and_batch_deletion`，原因 `requires disposable PostgreSQL server`。保留 Starlette/AnyIO 弃用警告。Ruff、OpenSpec strict、git diff 检查通过。

新增模块行/分支覆盖率：采集 99.00% / 96.15%，读取 96.00% / 91.25%。统一完整分母统计全部 Python 31.47% / 10.41%，项目目录及关键组门槛**未通过**，命令因此非零退出。详见 [完整统计](coverage-summary.md) 与 [测试输出](test-output.txt)。不能用两个模块的覆盖率替代整个 Worker/Runner 目录或业务执行关键组的门槛。
