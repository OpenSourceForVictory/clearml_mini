# 进程日志基础实现阶段验证（2026-11-19）

对应 OpenSpec `process-scoped-execution-logs`，仅完成部分基础代码，不代表整项功能验收或发布。新增执行目录组件、scope/episode/generation 契约、目录索引迁移、有限读取请求邮箱及批次删除关联处理。Runner/Worker 尚未接入采集和读取，未宣布新能力，未部署或升级节点。

## 验证结果

- 定向 Python 回归：25 passed，1836 deselected，1 warning，77.43 秒。命令：`PYTEST_ADDOPTS='--import-mode=importlib -k "process_log or execution_locations or execution_logs_v2 or batch_deletion"' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/process-log-foundation`。数据库指向本次独立 PostgreSQL 测试容器。
- 使用完整代码统计范围，覆盖率检查失败；[报告](coverage-summary.md)记录全部 Python 行覆盖率 33.71%、分支覆盖率 12.34%，业务执行与结果 27.39% / 13.46%。未降低门槛；这不是全量测试结果，也不能据此宣称覆盖率达标。
- PostgreSQL 迁移单测：1 passed。验证从 y518b90 升级到 z629c01 保留旧日志、新表关联删除及降级保留旧日志表。仅临时测试数据，不是生产备份恢复演练。
- 执行目录浏览器单测：1 passed，覆盖批次尝试切换、旧记录不猜目录、复制成功/失败。
- TypeScript 类型检查通过；Vite 构建通过，有大 chunk 警告。前端未插桩，未计入 Python 覆盖率。
- 最后检查补充清理 staging 目录路径契约；覆盖率报告先于该补充，不声称报告覆盖最终补充分支。最终执行目录定向测试 3 passed（3.55 秒）。

## 失败与边界

首次执行目录接口测试 1 failed / 1 passed：API 注册位于前端静态挂载之后导致 404；已将注册移到静态挂载前，随后接口测试通过。该首次失败未保存独立原始日志，此处记录触发原因，不能替代原始 Traceback。

首次前端构建命令因环境缺少 npm 返回 127；改用已安装 Node 直接执行 TypeScript/Vite 后通过。完整本地输出保留在 `.runtime/process-scoped-execution-logs/`，该目录不提交。

整项 OpenSpec 任务仍未勾选：尚缺采集、磁盘预算、多进程协调、节点读取、防竞态清理、日志抽屉、压力及真机验收。需要确认 Runner 与 Worker 的落盘职责：普通业务账号不能直接修改 Worker 私有的整机配额账本。建议受限本机通道由 Worker 统一落盘；待确认后更新设计和测试矩阵再接入。
