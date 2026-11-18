# 服务实例批次删除外键回归

2026-11-18：调度实际创建服务实例和服务任务后，旧代码删除批次触发 `DELETE FROM service_instances` 外键失败，证据见 [failure.txt](failure.txt)。原编排删除测试在调度前取消，未覆盖实际服务记录。

修正：每层删除显式 flush，先删除子记录再删除父记录；仍仅在接口末尾 commit，保持事务原子性。无需迁移或 Worker 更新，需发布平台 API。

验证命令：

```bash
PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_batch_deletion.py backend/tests/test_batch_deletion_records.py -q
```

结果：9 passed，1 项现有 Starlette 弃用警告，见 [tests.txt](tests.txt)。包含实际调度产生的数据库服务记录、SQLite 外键约束、重复删除、审计失败后的整体回滚及重试；清理确认由测试模拟，没有启动真机业务。

未执行全量覆盖率统计，不能据此宣称达到项目目录或关键功能覆盖率门槛。未验证现场 PostgreSQL 或部署到用户故障平台，现场 500 仍需对应 Traceback 核对。界面流程和按钮未改变，现有界面指南的删除条件与错误排查说明仍适用。
