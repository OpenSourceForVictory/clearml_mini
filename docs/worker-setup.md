# Worker 安装

每张 GPU 启动一个 Worker 实例。Worker 本身只用 Python 标准库，不需要安装 ClearML SDK 或其他 Python 包。控制节点无 GPU 也可运行。

## 准备

- Linux、Python 3.11+（当前标准库客户端也已在本机 Python 3.9 上用于 smoke）。
- 当前账户可运行 `docker info`。
- NVIDIA GPU：`nvidia-smi` 正常，Docker 已配置 NVIDIA Container Toolkit。
- 预先构建或拉取固定 Runner 镜像。不要在任务领取期间临时拉取大镜像。

```bash
docker build -f runner/Dockerfile -t mini-eval-runner:dev .
# 仅 GPU Worker 需要此验证
docker run --rm --gpus all mini-eval-runner:dev nvidia-smi
```

CPU 架构不同的机器需要各自架构的 Runner 镜像，可在目标机器原生构建，或使用内部仓库的多架构镜像。`RUNNER_IMAGE` 与 Worker 配置的 `runner_image` 必须一致。

需要 RoboTwin GPU 环境自检时，使用 [渲染检查镜像及配置](robotwin-render.md)。该文档也说明可选的 `gpu_runtime: nvidia` 配置。

## 配置和安装

在 Web 管理页面创建资源池和机器后下载配置。`api_url` 必须能从 GPU 机器访问；浏览器在控制节点本机打开时，下载配置可能是 `127.0.0.1`，需改成私网地址。配置含 Worker token，文件权限应为 0600。

```json
{
  "api_url": "http://control-private-ip:8000",
  "token": "填写管理页面生成的凭据",
  "node_name": "gpu-node01",
  "pool_id": "填写资源池 ID",
  "gpu_index": 0,
  "mock": false,
  "runner_image": "mini-eval-runner:dev"
}
```

GPU 型号和 UUID 由 `nvidia-smi` 获取；资源池型号须与返回值一致，显存按 GiB 向下取整报告。mock 配置使用 `mock: true` 和 `virtual` 资源池；这只模拟执行槽位，不代表真实 GPU 已验收。

```bash
python scripts/install_worker.py --config worker-node01-0.json --instance gpu0 --check
python scripts/install_worker.py --config worker-node01-0.json --instance gpu0 --start
# 多卡：为 GPU 1 下载相应配置，再启动第二个实例
python scripts/install_worker.py --config worker-node01-1.json --instance gpu1 --start
```

安装位置：程序在 `~/.local/share/mini-eval-worker/`，配置在 `~/.config/mini-eval/`，执行记录在 `~/.local/state/mini-eval/`，systemd 单元在 `~/.config/systemd/user/`。不要在任务运行中删除执行记录或容器。

```bash
systemctl --user status mini-eval-worker@gpu0
journalctl --user -u mini-eval-worker@gpu0 -f
systemctl --user restart mini-eval-worker@gpu0
```

需要退出登录后持续运行时，由机器管理员为该账户启用 systemd lingering。没有用户 systemd 的机器可省略 `--start`，按脚本打印的 Python 命令前台运行或交给现有进程管理器。

## 凭据与恢复

禁用机器会拒绝其 Worker 认证，Worker 会停止容器；重新启用后会恢复状态对账。token 丢失时管理员可调用 `POST /api/v1/admin/nodes/{id}/rotate-token` 重新生成，并更新所有该机器的 Worker 配置。旧 token 立即失效。

Worker 正常退出会尝试确认清理。突然掉电或 SIGKILL 后，服务端在租约和宽限期后隔离槽位；重启原 Worker 可停止遗留容器并解除隔离。任务重试前必须先完成这一步。

## 异构池和同卡共享

新 Worker 自动声明协议 v2，每卡保持一个进程，按执行 ID 保存独立 journal 和租约。通过 `nvidia-smi` 上报每卡空闲显存与利用率，采集失败时暂停该卡的新共享准入。`--once` 只处理一个执行后退出；常驻服务支持多执行。

管理员在资源池开启共享并设置每卡上限，同时提高节点并发额度；任务需要声明显存需求。更改配置 `pool_id` 后可将空闲卡迁入同一异构池，忙卡拒绝迁移。不要删除 `.state.runs` 内的恢复记录。详见 [GPU 调度](gpu-scheduling.md)。
