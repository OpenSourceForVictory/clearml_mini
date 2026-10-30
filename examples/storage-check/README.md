# 存储验收客户端

此客户端只检查资产/缓存只读、工作/输出可写及 SDK 回合上报，不运行机器人或推理模型。

在独立测试部署上运行 `scripts/smoke_storage.py`；不要指向生产 API。先构建镜像并在两台测试主机准备同一镜像：

```bash
docker build -t mini-eval-storage-check:1 -f examples/storage-check/Dockerfile .
```

脚本使用 `EVAL_API_URL`、`ADMIN_USERNAME`、`ADMIN_PASSWORD`、`STORAGE_TEST_HOST`（本机可被两端容器访问的局域网地址，测试 TCP 服务使用 19091 端口），可通过 `--remote`/`--identity` 指定直连 SSH 主机与密钥。远端通过脚本建立的仅监听回环地址的反向隧道连接测试 API。测试使用虚拟 GPU 身份和真实 Docker 容器，不占用生产 GPU 槽位。
