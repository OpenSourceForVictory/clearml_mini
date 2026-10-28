# ClearML Mini Eval Platform

项目正在实现中。需求基线见 `IMPLEMENTATION_SPEC.md`；OpenSpec 规范见 `openspec/`；当前完成度和风险见 `docs/operations.md`。

设计以单张物理 GPU 为一个槽位。单卡机器注册一个，多卡机器逐卡注册，型号和显存归属资源池。每槽位预留独立 ClearML 队列。RoboTwin 适配器目前只是明确的接口占位，尚未经过真实 GPU 验收。

开发依赖列于 `backend/requirements.txt`。当前环境没有安装 Python 依赖和 Docker，尚未运行 API 集成测试或 Compose 验证。

OpenSpec CLI 需使用 Node 24（项目 `.nvmrc`）。当前账户可执行 `export PATH=/data5T/d00942093/.local/node24/bin:$PATH` 后运行 `npx @fission-ai/openspec@latest status --change initial-mvp`。平台功能按 `openspec/changes/initial-mvp/tasks.md` 的 P0 → P1 → P2 顺序实施；任务只有在代码与对应测试均完成后才勾选。

Codex CLI 要在本项目目录启动才能发现 `.agents/skills` 中的 OpenSpec 工作流。进入项目后运行 `codex`，在交互界面输入 `/skills` 或 `$openspec-apply-change`。OpenSpec 的终端命令是独立的 `openspec` CLI，不会变成 Codex 的 `/openspec` 命令。

多用户账号由管理员在控制节点创建：设置 `USERNAME` 和 `USER_PASSWORD`（至少 12 字符），再运行 `python -m scripts.create_user`。首次引导管理员则设置 `ADMIN_USERNAME`、`ADMIN_PASSWORD` 并运行 `python -m scripts.bootstrap`；运行脚本前必须先完成 Alembic 迁移。
