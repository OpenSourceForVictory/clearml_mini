# 运行状态

当前项目是初始工程，尚未达到 `IMPLEMENTATION_SPEC.md` 的 MVP 验收标准。API、数据库模型、保守调度器和 mock runner 有代码，但缺少 Alembic 迁移、管理员引导、真实 ClearML 适配器、对账、前端和 RoboTwin 执行环境。不要把此版本接入真实 GPU Agent。

数据库和 ClearML 之间没有共享事务。现有调度器还不能证明崩溃恢复后不重复投递，因此生产调度必须等待 stable marker 对账和故障注入测试完成。

文档管理：需求基线在 `IMPLEMENTATION_SPEC.md`，逐项能力在 `openspec/specs/`，变更计划在 `openspec/changes/`。修改行为时同步更新对应规范和任务清单。

启动 API 前设置 `DATABASE_URL`、`JWT_SECRET`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`，执行 `python -m scripts.bootstrap`。Compose 中暂不启动调度器，以免 FakeAdapter 将数据库任务标记为已投递而实际没有远程执行。
