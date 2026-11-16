# Ascend ACL HTTP 服务示例

服务通过 ctypes 调用 CANN，验证仅看到一张分配设备并申请少量 NPU 内存，提供 HTTP 健康检查和 JSON 回显。它用于验证平台调度及网络，不加载真实模型。

业务参数 `model` 和 `allocation_mb` 映射到 `--model`、`--allocation-mb`；端口、监听地址、执行 ID 由平台输入协议提供。宿主机使用批准的 `ascend_cann` 环境；容器用本目录 Dockerfile 构建，在 1.95.69.29 上已准备 `mini-eval-npu-service:demo-v1`。

```bash
docker build -t mini-eval-npu-service:demo-v1 examples/ascend_service
```

宿主机命令为 `python3 server.py`（业务目录中的副本），容器命令为 `python3 /demo/server.py`。默认每个实例申请 1 CPU、2 GiB 主机内存和 1 GiB 共享 NPU 预算，实际用于功能探测的设备内存仅 8 MiB。不要把预算准入当作设备显存硬隔离。模型接入时应替换业务程序并按实际需求设置资源。

节点接入和转发配置见 [服务端操作说明](../../docs/ascend-server-node.md)。
