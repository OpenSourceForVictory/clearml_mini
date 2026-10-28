# ClearML Mini Eval Platform

项目正在实现中。需求基线见 `IMPLEMENTATION_SPEC.md`；OpenSpec 规范见 `openspec/`；当前完成度和风险见 `docs/operations.md`。

设计以单张物理 GPU 为一个槽位。单卡机器注册一个，多卡机器逐卡注册，型号和显存归属资源池。每槽位预留独立 ClearML 队列。RoboTwin 适配器目前只是明确的接口占位，尚未经过真实 GPU 验收。

开发依赖列于 `backend/requirements.txt`。当前环境没有安装 Python 依赖和 Docker，尚未运行 API 集成测试或 Compose 验证。
