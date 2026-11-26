# Worker 安装

每张 GPU 启动一个 Worker 实例。Worker 本身只用 Python 标准库，不需要安装 ClearML SDK 或其他 Python 包。控制节点无 GPU 也可运行。

## 准备

- Linux、Python 3.11+（当前标准库客户端也已在本机 Python 3.9 上用于 smoke）。
- 当前账户可运行 `docker info`。
- NVIDIA GPU：`nvidia-smi` 正常，Docker 已配置 NVIDIA Container Toolkit。
- 节点可先纳管；缺少 Runner 镜像不阻止安装。运行容器任务及容器存储校验前，由用户构建或拉取固定 Runner 镜像。不要在任务领取期间临时拉取大镜像。

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

Worker 执行 journal 在写入临时文件后同步文件，再原子重命名并同步父目录；本地持久化失败必须报错，不能视为已可靠记录并继续启动。文件系统须支持目录 fsync；若出现相关 I/O 错误，先修复节点存储，不要删除恢复记录。

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

多卡机器的顶部表单是**整机默认配置**，只作用于没有单独设置的设备；没有整机配置时不会再用 GPU/NPU 0 的实际路径预填。每张卡下方的“修改 GPU/NPU N 目录”可以分别保存工作基目录、资产目录、缓存目录和空间门槛，也可恢复整机默认或本机初始配置。“采用建议工作目录”会在当前路径下加入机器名和卡号；保存前须确认目标盘和权限。平台仍在所填工作基目录下追加 `/slots/<slot_id>`，确保同名或重建后的设备执行目录不碰撞。更改一张卡会递增整机存储版本，其他卡也要重新上报确认；已领取任务继续使用原目录，未领取的该卡预留任务退回等待。旧目录及数据不会自动搬迁或删除。

管理员专用接口 `PUT /api/v1/admin/nodes/{worker_id}/storage/slots/{slot_id}` 沿用 `expected_revision` 与完整 `config`，`config: null` 清除此卡覆盖。`GET /api/v1/admin/nodes/{worker_id}/storage` 的 `config` 仍是整机默认，`slots[].config` 是单卡覆盖；Worker 心跳只接收本槽位解析后的配置。覆盖配置保存在节点现有连接元数据中，不改数据库结构或 Worker 协议。回退旧 API 版本时旧服务忽略单卡覆盖，需先恢复新版 API 才能继续管理该覆盖；不要假定旧 API 会保留每卡目录调度行为。

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

自带客户端镜像、启动变量、跨节点逻辑资产映射与 Worker v5 配置见 [镜像客户端指南](image-client-runtime.md)。

## 执行数据管理（v6）

升级后的 Worker 可后台盘点执行目录；管理员在机器的“执行数据”入口预览并提交清理。自动保留默认关闭，空间不足不会自行删除数据。范围、协议和回退见 [执行数据保留文档](worker-retention.md)。

SSH 纳管界面提示执行器未配置或不可用时，检查 [SSH 纳管执行器部署](ssh-onboarding-executor.md)。纳管执行器与节点 Agent 是两个不同程序，前者运行在平台主机。

新节点推荐从机器管理使用 root SSH 纳管，安装系统 Agent/Worker，宿主机业务统一由 mini-eval 普通账号运行。账号、NFS UID/GID、目录权限和旧节点迁移限制见 [root 纳管指南](root-managed-host.md)。

## 可选业务 PID 上限

Worker 0.21.4起，宿主机和Docker业务默认显式取消执行实例PID上限（TasksMax=infinity、--pids-limit=-1），不再强制256。每卡Worker JSON顶层 `execution_pids_limit` 可设null或正整数，空闲重启后新任务生效；不是环境变量，旧Worker不会读取它。配置、线程计数及验证命令见[业务进程与线程上限](host-execution.md#业务进程与线程上限worker-0214)。

## 独立日志预算

Worker 0.21.6 默认不限制整机日志总量；仍保留每分片 4 MiB、每 scope 16 MiB、每执行 256 MiB 的预算，避免单次输出无界增长。配置文件可使用 `process_log_policy` 对象设置 `segment_bytes`、`scope_bytes`、`execution_bytes`，均为整数，要求 65536 ≤ 分片 ≤ scope ≤ 执行 ≤ 1 TiB。0.21.6–0.21.10 使用本地配置，在新执行初始化时快照，修改后重启 Worker，不改变活动执行预算。0.21.11 使用下节平台版本同步，平台已确认配置优先于本地值；不接受有限 node_bytes 配额。布局和保留边界见 [执行日志](execution-logs.md)。

## 独立日志预算同步

管理设置 → 机器 → 查看配置 → 数据保留 → 展开执行数据，可设置日志分片、单份日志、单次执行的字节预算（界面单位 MiB）。三者递增，分片至少 64 KiB，最大 1 TiB。整机总量不限制；这不影响已有执行数据保留策略。

保存只增加平台期望版本；每张卡的 Worker 0.21.11（process_log_policy_v1）独立持久化并回报生效版本。旧 Worker 显示需要升级。应用失败保留上次已确认配置，不把保存成功当成节点已生效。API 不等待节点磁盘操作。执行首次领取时冻结该卡已确认版本，重领、恢复以及之后降额都不改该执行；降额仅影响后续执行，不立即清理历史文件。配置同步在独立日志线程，最多每 30 秒一次，失败不阻断心跳和业务。

该配置需要数据库 b841e23；按[迁移指南](database-migration.md)先迁移再更新 API，随后按[节点升级说明](worker-upgrades.md)选择节点升级。Worker 状态文件同级的 `.log-policy` 保存本卡回执，不修改业务账号的 shell 环境。

## Worker 基础心跳周期（0.21.21 起）

新 Worker 默认每约 5 秒发送基础心跳，成功发送后随机错峰到 4.5～5.5 秒。首次注册后立即上报；任务领取仍独立按原有最多 2 秒常态轮询，执行续租/取消控制的 2 秒周期和本机进程检查的 0.5 秒周期不变。网络请求耗时和失败退避会增加实际间隔，5 秒不是硬实时保证。

Worker 实际配置 JSON 可选添加数值字段：

```json
{
  "worker_heartbeat_seconds": 5,
  "worker_report_seconds": 20
}
```

基础周期允许 2～10 秒，完整报告周期允许 10～30 秒；不接受字符串、布尔值、非有限数或越界值。未配置时直接使用默认值，无需修改每台机器配置。手动修改后重启对应 Worker systemd 服务；本次未增加界面编辑入口。升级 Worker 后生效，仅更新 API 不会改变旧 Worker 的周期。协议版本仍为18，无数据库迁移。

software、storage、runtime、host 回执有变化时，在下一次主循环及时发送；未变化时每20秒完整刷新。磁盘容量变化不会单独触发完整回执，但会随完整报告更新；disk/telemetry仍随基础心跳上报，采样失败及时失效。服务端原有环境60秒、资源45秒有效期不变。管理配置由心跳响应下发，发现新配置的等待可能增加到一次基础周期加网络耗时；领取接口仍按服务端最新授权和配置验收状态判断，不能仅凭本地缓存启动任务。

该周期不控制日志、结果、任务执行超时或断联容忍期限。完成/失败/清理上报不等下一次基础心跳；429/503等沿用有界退避和Retry-After，失败的完整回执不会被当作已确认。已有任务的执行控制独立继续，不会因减少基础上报而改变取消、断联恢复或清理规则。

完整报告到期后合并到下一次基础心跳，不为周期刷新单独发请求；默认完整回执间隔不超过约25.5秒加请求耗时。允许配置上限30秒+11秒仍小于服务端60秒环境有效期。配置/检查状态变化仍可提前触发。
