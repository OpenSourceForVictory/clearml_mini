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

GPU 型号和 UUID 由 `nvidia-smi` 获取，显存按 GiB 向下取整报告。物理资源池使用 `gpu_model=*`，允许不同机器、不同 GPU 型号加入同一个池。mock 配置使用 `mock: true` 和 `virtual` 资源池；这只模拟执行槽位，不代表真实 GPU 已验收。

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

当前 Worker 自动声明协议 v4（包含此前 v2 的共享调度与 v3 的客户端能力），每卡保持一个进程，按执行 ID 保存独立 journal 和租约。通过 `nvidia-smi` 上报每卡空闲显存与利用率，采集失败时暂停该卡的新共享准入。`--once` 只处理一个执行后退出；常驻服务支持多执行。

管理员在资源池开启共享并设置每卡上限，同时提高节点并发额度；任务需要声明显存需求。更改配置 `pool_id` 后可将空闲卡迁入同一异构池，忙卡拒绝迁移。不要删除 `.state.runs` 内的恢复记录。详见 [GPU 调度](gpu-scheduling.md)。

## CS 仿真客户端接入补充

在下载的 Worker JSON 中指定固定工作目录与允许执行的客户端镜像，例如：

```json
{
  "workspace_root": "/home/huawei/mini-eval-workspaces/gpu0",
  "client_images": ["mini-eval-robotwin-queue-check:1"],
  "min_free_disk_gb": 2
}
```

这些是补充字段，不能替代前面的完整配置。客户端镜像需提前在该主机构建或拉取，仿真资产默认放在工作目录的 `assets/` 下，也可通过下方存储配置设置独立目录；只登记平台模板并不会自动分发镜像或资产。更新安装后的配置后重启对应实例，使镜像能力重新上报。

Worker 主动通过 HTTP API 注册、心跳和领取任务；平台不需要保存机器的 SSH 密码。注册后按物理 GPU UUID 识别执行槽位，并上报型号、显存、利用率、磁盘和客户端镜像能力。平台匹配资源池、镜像及额度后，Worker 领取任务并启动容器，上报日志、回合进度与结果。离线恢复依赖持久化执行记录，不要手动删除正在使用的状态文件。

接入完成后，在「计算资源」确认节点与 GPU 在线、型号及资源池正确，并确认客户端镜像已上报，再提交小集合验证。具体工作目录布局见 [评测框架](evaluation-framework.md#worker-固定工作目录)，双机样本见 [RoboTwin 排队验收](robotwin-queue-validation.md)。

## 注册后在界面配置目录（协议 v4）

管理员进入「管理设置 → 已注册机器 → 机器名 存储配置」，填写工作基目录、资产目录、缓存目录和工作盘最低可用 GiB。保存的是**期望配置**；逐卡显示待校验、等待任务结束、校验失败、已生效、离线或需升级。多卡可能部分生效，不能仅凭保存提示判断部署完成。轮询不会覆盖正在编辑的草稿；遇到并发修改冲突，先重新载入最新配置再保存。

- 工作基目录可自动创建，每卡使用 `<工作基目录>/slots/<slot_id>/runs/<execution_id>/{work,output}`。
- 资产、缓存必须在宿主机预先存在、可遍历读取，容器中只读挂载。不同机器可选择不同路径，不要求放在工作目录内。
- 容器内固定为 `/opt/mini-eval/assets`、`/opt/mini-eval/cache`、`/tmp/mini-eval-work`、`/tmp/mini-eval-output`。具体资产子目录由客户端定义，例如 RoboTwin 的版本化资产包。
- 不能使用相对路径、根目录、软链接（包括父目录）、逗号、控制字符、`..`，不能挂载系统配置、特殊文件系统或凭据目录；共享目录不能包含执行工作树、Worker 配置或恢复记录。
- Worker 使用本机配置的 `runner_image` 做受限挂载探测：不拉镜像、不使用网络/GPU，以低资源、非 root 用户检查共享挂载只读及 work/output 可写。镜像缺失、权限或超时错误会显示在界面；修正后再次保存会产生新版本并重试。
- 工作盘空间不足或存储报告超过 15 秒未更新，会暂停新任务准入；原任务继续。恢复容量后自动恢复准入；平台不自动清理、移动或删除旧数据。

`state_path` 始终使用启动时的位置，不随界面设置迁移。相邻的 `.storage.json` 仅保存初始/生效存储配置及版本，不含 token；每次执行的 journal 保存路径快照。升级时旧 journal 会补写初始路径，重启恢复仍使用原目录。

「恢复本机配置」同样产生新版本，等待任务清理及挂载验证。这里的本机配置指首次启用 v4 时持久化的初始值，而不是随后手动修改 JSON 后的值。请保留初始目录和恢复文件，不要在有活动任务时删除它们。v1–v3 在未设置远程配置的机器上继续工作；设置远程配置后须升级 Worker 到 v4 才能恢复新任务准入。

## 平台管理 GPU 归属

配置可以省略 pool_id，接入后从管理设置选卡分配。多卡安装、API 地址确认、排空迁移与回退见 [资源池成员管理](resource-pool-membership.md)。默认真实 GPU，模拟设备须显式设置 mock=true。
