# 宿主机 missing unit 恢复专项（2026-11-23）

## 症状与修正

现场提供 `execution_id=e573556a-da93-4179-af6f-e4d8024a5344; service_instance_id=unknown; source=journal-created`。这表明本地 journal 已标记创建，但恢复检查找不到可恢复的 unit；不能据此证明服务曾成功启动，也不能推断实例重名。unknown 表示缺少服务实例身份。

复现的代码缺口：Worker 在 create 前保存 created，宿主机准备或 start 的 PermissionError/OSError/TimeoutError 被外层通信异常分支接走，未保存 terminal_failure。下次恢复可能只剩 missing unit。0.21.14 在宿主机本地 create/start 边界将这些错误转为终态启动错误，先保存首错，完成请求失败后重放同一错误；控制面网络错误仍可重试，Docker 分支保持原行为。

现场最初的权限、存储或启动失败原因尚未取得，以上为代码复现，不是现场根因确认。硬中断、账本不可写、unit 被外部清理等情况下仍可能触发防重复执行保护；不会自动清除 created 后再次运行。

## 测试与证据

- 修改前基线：五个恢复相关测试文件，126 passed。
- 新增首错复现：修复前 6 failed，均因 terminal_failure 未保存；本机原始证据 `.runtime/host-missing-20261123/startup-oserror-before.txt`（不提交运行目录）。
- 修复后相同五文件加新增用例：148 passed，18.48 秒；升级专项另 8 passed、1 条第三方弃用警告；前端 `tsc --noEmit` 通过。
- 覆盖 user/system manager、创建标记/准备完成/启动标记/退出记录四个中断窗口、目录快照恢复、manager 不可用/拒绝访问、本地失败加完成接口 503 后重放、心跳通信故障、Docker 原策略。
- 使用真实临时文件和 journal 重载，systemctl/systemd-run 边界模拟。中断使用 BaseException 模拟，不是真机断电或 SIGKILL 验收。本次未部署节点，未运行全量测试。

定向命令：

```bash
COVERAGE_FILE=.runtime/host-missing-20261123/final.coverage \
  ./.venv/bin/python -m coverage run --rcfile=backend/tests/project.coveragerc -m pytest -q \
  backend/tests/test_host_failure_recovery.py backend/tests/test_worker_execution_recovery.py \
  backend/tests/test_host_backend_lifecycle.py backend/tests/test_worker_service_execution.py \
  backend/tests/test_worker_recovery.py
./.venv/bin/python -m pytest -q backend/tests/test_worker_upgrades.py
```

## 覆盖率边界

沿用统一覆盖率配置，不排除业务代码；只选择上述专项测试，未以通过数量代替覆盖率。函数统计为辅助定位，不能替代 `python -m scripts.test_coverage` 的目录及关键功能组验收。

| 范围 | 基线行/分支 | 修复后行/分支 |
| --- | --- | --- |
| worker/client.py 全文件 | 65.96% / 53.69% | 66.44% / 55.08% |
| Worker.execute | 86.08% / 77.94% | 86.48% / 78.99% |
| worker/host_backend.py 全文件 | 43.93% / 35.45% | 45.19% / 36.36% |
| HostBackend.inspect | 100% / 100% | 100% / 100% |
| HostBackend.start | 100% / 100% | 100% / 100% |

inspect/start 原先已达 100%，仍缺跨函数恢复窗口，说明覆盖率不能替代场景验证。本轮定向结果未达到整模块门槛，也不能宣称目录/关键功能组达标；本机保留 baseline/final 的 coverage、JSON、JUnit 及 summary.json。没有降低门槛或改动排除规则。

## 更新范围

Worker 0.21.14，协议仍为 18，无数据库迁移。已纳管节点需要更新实际运行的 Worker，旧 journal 中丢失的首错无法追溯恢复。同步宿主机指南、升级说明、界面指南及 Skill 实现索引。
