# 本地 GPU 集群部署

控制节点：192.168.8.151，浏览器通过 SSH 转发访问 127.0.0.1:18000。Worker 使用 http://192.168.8.151:18000。本地库是独立新库，未迁移 R8000 历史数据。

## 主机清单

| 平台名称 | IP | GPU | 显存 MiB |
|---|---|---|---:|
| local4090-151 | 192.168.8.151 | RTX 4090 | 24564 |
| rtx8000-177 | 192.168.8.177 | Quadro RTX 8000 | 49152 |
| rtx3080-182 | 192.168.8.182 | RTX 3080 | 10240 |
| rtx4090-183 | 192.168.8.183 | RTX 4090 | 24564 |
| rtx4090-157 | 192.168.8.157 | RTX 4090 | 24564 |

192.168.8.174 当前无路由，未注册为在线节点。恢复网络后使用 [Worker 安装说明](worker-setup.md) 接入。

五台加入「公共 GPU 评测池」，型号为 `*`，默认 GPU 独占、每机并发 1；允许不同型号共池。用户可在任务中限制型号或申请资源，平台按资源约束排队。

## 启动与访问

`.env` 中 `API_BIND_HOST=127.0.0.1`、`API_BIND_PORT=18000`、`WORKER_BIND_HOST=192.168.8.151`。LAN overlay 只添加指定私网地址，不监听所有网卡。

```bash
docker compose --env-file .env -p mini-eval -f deploy/compose.yml -f deploy/lan-compose.yml up -d --no-build
```

普通 `control.py up` 不加载 LAN overlay；维护此多机部署请使用上述命令。移除私网映射时省略最后一个 `-f` 并重建 API；Worker 将离线，先排空任务。

个人电脑沿用 SSH 用户名、地址、私钥，在原连接加转发：

```bash
ssh -p 10001 -N -L 127.0.0.1:18000:127.0.0.1:18000 用户名@SSH地址
```

浏览器打开 http://127.0.0.1:18000。平台监听地址变化时同步更新 `.env` 和每台 Worker 的 `api_url`，保留 node_name/token/state_path；等待执行完成后重启 Worker。

## Worker 运维

本机用户 dgh，远程用户 huawei。用户 systemd 单元 `mini-eval-worker@fleet-gpu0`；启用 linger，可在退出 SSH 后持续运行并随系统启动。程序位于 `~/.local/share/mini-eval-worker/`，配置 `~/.config/mini-eval/fleet-gpu0.json`（0600），恢复状态 `~/.local/state/mini-eval/fleet-gpu0.json` 及相邻目录。每台使用独立 Worker token，平台不保存 sudo 密码。

```bash
systemctl --user status mini-eval-worker@fleet-gpu0
journalctl --user -u mini-eval-worker@fleet-gpu0 -n 80
```

本机无交互登录环境时先设置 `XDG_RUNTIME_DIR=/run/user/$(id -u)` 和 `DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus`。

平台存储配置：工作基目录 `~/mini-eval-fleet/work`（配置中使用绝对路径），资产 `~/mini-eval-fleet/gpu0/assets`，缓存 `~/mini-eval-fleet/gpu0/cache`，最低剩余 5 GiB。资产目录初始为空，需要具体仿真资产时再准备和映射；不要删除 journal 绕过清理。

三台新节点安装 Ubuntu docker.io 和 NVIDIA Container Toolkit，未升级 GPU 驱动；按 [NVIDIA 官方安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) 配置 NVIDIA runtime。统一预置 `mini-eval-runner:dev`（本次构建并分发的镜像），同时允许其作为轻量 SDK 客户端。大型 RoboTwin/Cosmos 镜像与资产没有自动复制到新主机。

R8000 原系统 Worker `mini-eval-worker-r8000` 已确认无活动容器和恢复 journal 后停用；原配置及工作目录保留。旧服务指向已失效的 192.168.137.223，不应与新服务同时运行。回退先排空并停止新用户服务，再恢复原控制节点及配置。

## 安装器修正

实际安装暴露 systemd 249 不接受带字面引号的 WorkingDirectory。安装器改为输出绝对路径并转义 `%`；新增包含空格与百分号的目录场景，用真实 systemd-analyze 校验生成单元。ExecStart 参数仍按原方式引用。

## 验收

运行日志与私密配置保存在本机 `.runtime/fleet-onboarding/`（Git 忽略）。不要将此目录整体加入版本管理。

2026-11-13 验收：5 个真实 GPU 容器均可运行 nvidia-smi；5 个 Worker 在线且均加入公共池，目录配置全部 applied。提交 7 项轻量 SDK 协议自检，观察到 5 个同时分配、2 个排队；最终 7/7 执行成功、7/7 回合报告齐全，覆盖全部 5 台主机。此结果只验证平台调度与容器链路，不代表模型质量或完整 RoboTwin 仿真验收。自检推理服务已停止，自检模板已禁用，历史批次可在平台查看。

平台全局及单用户并发上限设置为 5，每机上限 1；资源池仍不允许同卡共享。三台新装 Docker 的主机，原用户服务会话尚未继承新 docker 组，已在用户单元 drop-in `mini-eval-worker@fleet-gpu0.service.d/docker-group.conf` 中通过 `sg docker` 启动 Worker，未重启桌面或降低 Docker socket 权限。

验收批次 ID：`50f03e51-bcee-440e-abdd-b818cca6cbb9`。全部 Worker 重启后保持原 5 个 GPU slot 身份、心跳与存储回执正常，无遗留任务。安装器和成员管理回归 9 项通过，OpenSpec strict 与 git diff --check 通过。
