# 双宿主机 WebSocket 验收样例

这是轻量回显业务，不加载真实模型，也不执行 NPU 推理。两端 Python 环境均需安装 aiohttp；实际验证客户端使用 3.14.5，服务端使用已有的 3.14.3。

- 服务端模板：宿主机执行、直接协议、业务协议 `ws`。启动 `python3 server.py`（文件需位于批准的代码目录，也可将文件内容作为 `python3 -c` 参数）。程序从平台分配的环境变量读取监听地址、业务端口和独立健康端口。
- 服务端业务参数：`model=fleet-test-model`。健康检查选择 WebSocket 消息、路径 `/health`、JSON `/ready` 等于 `true`，独立健康端口 `8001`。前五秒报告未就绪，之后就绪。
- 客户端模板：宿主机执行、`sdk-command`、协议 `ws`。使用批准环境中的 Python 启动 `client.py`；每次调用执行一个回合，通过 `MINI_EVAL_RESULT` 提交成功和指标。
- 集合：三个 case，`case_id` 为 `0/1/2`，每个两回合，业务参数 `variant` 分别为 `0/1/2`。客户端从 `platform.service.endpoint` 获取平台分配的 WS 地址，自行附加业务路径 `/infer`。
- 向导：托管服务模式，客户端和服务端分别选择对应机器的资源池。共享/独占各提交一批，服务实例上限为 1。业务回显校验 case、回合、业务参数与服务执行标识。

实际验收将两个脚本内容保存进模板命令快照，以避免远端代码路径依赖。已验证 Worker 0.14.1 的宿主机客户端及服务端；详见 [执行证据](../../docs/validation/service-readiness/host-ws-evidence.json)。验收后恢复临时宿主机环境配置并停用测试模板，历史批次保留。
