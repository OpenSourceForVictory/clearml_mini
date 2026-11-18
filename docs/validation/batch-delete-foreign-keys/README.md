# 服务实例批次删除外键回归

2026-11-18：调度实际创建服务实例和服务任务后，旧代码删除批次触发 `DELETE FROM service_instances` 外键失败，证据见 [failure.txt](failure.txt)。原编排删除测试在调度前取消，未覆盖实际服务记录。

修正：每层删除显式 flush，先删除子记录再删除父记录；仍仅在接口末尾 commit，保持事务原子性。无需迁移或 Worker 更新，需发布平台 API。

验证命令：

```bash
PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_batch_deletion.py backend/tests/test_batch_deletion_records.py -q
```

结果：9 passed，1 项现有 Starlette 弃用警告，见 [tests.txt](tests.txt)。包含实际调度产生的数据库服务记录、SQLite 外键约束、重复删除、审计失败后的整体回滚及重试；清理确认由测试模拟，没有启动真机业务。

未执行全量覆盖率统计，不能据此宣称达到项目目录或关键功能覆盖率门槛。该专项测试本身不包含 PostgreSQL；后续当前平台实测见下文，其他部署仍需对应 Traceback 核对。界面流程和按钮未改变，现有界面指南的删除条件与错误排查说明仍适用。

## 当前平台发布与 PostgreSQL 验证

当前平台 API 日志确认删除批次 `36e0335c-67f8-447e-a729-9a6502309450` 返回 500，PostgreSQL 报 `evaluation_jobs_service_instance_id_fkey` 外键冲突，与本次复现一致。运行中的旧镜像尚未包含 fe7886e。

发布镜像 `mini-eval-api:batch-delete-fe7886e-20261118` 以先前 startup-lease-0205 镜像为基础，仅替换 backend/app/batch_deletion.py。发布前比对 API 环境配置、确认零活跃执行、暂时关闭节点准入并备份数据库；只重建 API，健康后恢复全部 6 节点原准入状态。无需迁移、前端重建或节点升级。备份与私有操作记录留在 .runtime/batch-delete-release-20261118，不提交。

重试用户已尝试删除的上述批次，API 成功返回 deleted=true、deleted_jobs=3、deleted_runs=3；重复删除同样成功。容器文件 SHA256 与提交代码一致，6 节点启用、held_executions=0。此为当前平台真实 PostgreSQL 删除验收，不代表其他独立部署已更新。
