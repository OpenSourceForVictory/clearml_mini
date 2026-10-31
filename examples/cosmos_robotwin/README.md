# Cosmos / RoboTwin 客户端样本

这是平台 SDK 的具体接入样本，定义了评测任务集合和客户端实现。**本次不需要提供服务端地址，也未运行真实模型闭环评测。** 运行时再填写推理服务地址。

上游：[causal_action_server.md](https://github.com/bevictory/cosmos-framework/blob/feat/robotwin-client-server/docs/causal_action_server.md)。固定版本：`2ba852c1ff3ce923405bd47a65074488a3358781`，不复制上游代码；镜像构建保留其仓库及许可证。

## 文件与用途

- `suite.json`：轻量自检子集，两个任务各 2 回合，仅 demo_clean/joint，seed=100000，max_steps=32。
- `suite-doc-matrix.json`：文档批量示例的完整展开：adjust_bottle / beat_block_hammer × demo_clean / demo_randomized × joint / eef，共 8 项、每项 3 回合，总计 24 回合，seed=100000，不额外限制 max_steps。
- `suite-joint.json`：适用于文档当前仅支持 joint 的 checkpoint，共 4 项、每项 3 回合，总计 12 回合。
- `suite-robotwin2-full.json`：由 RoboTwin `env_cfg/eval/all_tasks.yml` 生成并已注册平台，50 个官方任务，每项 100 回合（上游评测函数默认值），共 5,000 回合；固定 `demo_clean/joint`，配置版本对应 RoboTwin commit `ea8b21121ebb3cd201ff5b3fe361944ac94eda3f`。
- `client.py`：实现平台单回合接口，调用上游客户端并解析真实 metrics.json。
- `invoke.py`：为上游 WebSocket 连接和响应增加请求超时。
- `template.json`：管理员登记用的 SDK 模板，启用回合上报和推理端点独占。
- `Dockerfile`：从 PyTorch/CUDA 基础镜像构建完整 RoboTwin 仿真环境。
- `preflight.py`：不连接服务端的依赖与路径检查。

32 步用于验证一轮动作交互，并不保证任务有足够时间完成。正式评测可删除每项 parameters.max_steps，使用 RoboTwin 自身任务上限。两个任务名取自上游使用示例，所选 RoboTwin checkout 必须实际提供对应任务和配置。当前示例使用 joint；EEF 只有在模型 metadata 支持且变换配置正确时才可启用。

## 准备客户端环境

镜像内置 RoboTwin `ea8b21121ebb3cd201ff5b3fe361944ac94eda3f`、其固定 XPolicyLab 子模块、PyTorch 2.4.1/CUDA 12.1、SAPIEN 3.0.0b1、cuRobo v0.7.8、PyTorch3D v0.7.8 和 FFmpeg。NumPy 固定 1.26.4，避免原生扩展 ABI 冲突；CUDA 扩展编译支持 RTX 8000（sm75）和 RTX 4090（sm89）。宿主机只需兼容 NVIDIA 驱动、Docker 和 NVIDIA Container Toolkit，无需预装 RoboTwin Python 环境。

官方资产来自 `TianxingChen/RoboTwin2.0` 固定 revision `3dc3b798668feb99ac61cc9086d84cbcc3d79186`；独立下载器在宿主机下载机器人、物体、背景纹理三个压缩包并验证 SHA256。下载量约 15 GB，解压、CUDA 基础镜像和构建缓存还需额外空间。建议至少预留 80 GB 可用磁盘，资产下载可以在后台进行，不阻塞镜像构建。资产通过 Worker 已有的只读挂载映射到容器，镜像内 `/opt/RoboTwin/assets` 中的符号链接指向固定资产版本。

```bash
docker build -f examples/cosmos_robotwin/Dockerfile \
  -t mini-eval-cosmos-robotwin:2ba852c-platform1 .
```

网络需要时，可用 `--build-arg ROBOTWIN_BASE_IMAGE=m.daocloud.io/docker.io/pytorch/pytorch:2.4.1-cuda12.1-cudnn9-devel` 选择基础镜像源，或通过 Docker 标准 HTTP_PROXY/HTTPS_PROXY 构建参数配置代理。不要把代理凭据写入 Dockerfile。

镜像使用文档的 `examples/action_pretrain/robotwin_client.py` 入口；不修改其 `get_obs()`/`take_action()`。Cosmos 模型权重、统计、VAE 与 processor 留在外部推理服务。环境自检不连接推理服务，也不需要 EEF 配置：

```bash
docker run --rm --gpus all \
  --mount type=bind,source=/absolute/worker/assets,target=/opt/mini-eval/assets,readonly \
  mini-eval-cosmos-robotwin:2ba852c-platform1 \
  python -m examples.cosmos_robotwin.smoke_environment --task adjust_bottle
```

不依赖任何 RoboTwin 资产的基础检查可先执行：

```bash
docker run --rm --gpus all mini-eval-cosmos-robotwin:2ba852c-platform1 \
  python -m examples.cosmos_robotwin.smoke_environment --render-only
```

自检检查 CUDA、SAPIEN 物理/渲染、真实任务初始化及头部/双腕 RGB 图像。它属于环境验证，不产生模型成功率。另一个样本任务可用 `--task beat_block_hammer` 检查。构建时静态预检不替代这个 GPU 自检。

### 宿主机后台下载资产

在 Worker 宿主机执行（把路径替换为其 workspace_root）：

```bash
mkdir -p /absolute/worker/cache/robotwin
nohup python3 -m examples.cosmos_robotwin.assets \
  --assets-root /absolute/worker/assets \
  --cache-dir /absolute/worker/cache/robotwin \
  > /absolute/worker/cache/robotwin/download.log 2>&1 &
```

下载支持断点续传，压缩包保留在 cache；校验并解压至隐藏临时目录后，原子发布 `assets/robotwin/3dc3b798668feb99ac61cc9086d84cbcc3d79186/manifest.json`。已有版本不覆盖、不自动删除。manifest 仅供下载器记录和校验完整发布版本，不作为平台调度或客户端启动的准入条件。模板中的机器人路径写为容器路径 `/opt/RoboTwin`，不会绑定某台宿主机的绝对路径。

镜像构建、平台调度和客户端启动不检查整套资产是否完成。具体任务由 RoboTwin 在使用文件时报告缺失；不存在的必需模型或物体不会被假定为可用。手动 `docker run` 使用上面的 `--mount`，平台 Worker 已自动挂载其 assets 目录，无需每次提交任务额外指定宿主机路径。

将匹配训练坐标系的 EEF JSON 放入各 Worker 的 `workspace_root/assets/cosmos/eef.json`。上游代码强制要求 EEF 配置或数据集坐标元信息，即便使用 joint 控制也会发送 endpose。**不能猜测 T_world_base 或用单位变换冒充真实配置。** world/base 的选择必须与训练数据一致；配置格式由上游 RobotwinPoseConverter 校验。

```bash
docker run --rm \
  --mount type=bind,source=/absolute/worker/assets,target=/opt/mini-eval/assets,readonly \
  mini-eval-cosmos-robotwin:2ba852c-platform1 \
  python -m examples.cosmos_robotwin.preflight
```

管理员将镜像分发到执行节点并加入 Worker 的 client_images。确认依赖就绪后才登记 template.json；镜像构建和 GPU 自检未通过时，不应将样本标成可在线执行。

## 定义集合与发起运行

在平台选择登记后的 Cosmos 模板，导入所需集合 JSON，保存集合名称和版本。RoboTwin 完整任务集合已按上游 `env_cfg/eval/all_tasks.yml` 注册平台；首次环境联调可选 suite.json；按文档当前 checkpoint 评测可选 suite-joint.json；只有服务 metadata 同时支持 joint/eef 时才选择 suite-doc-matrix.json。每个 task/config/mode 组合是独立平台子任务，由平台统一排队、限制并发并跟踪回合进度，避免一个进程内隐藏整个集合。这一步不需要推理服务地址。以后发起运行时填写 `ws://模型机器:端口`、资源池和资源请求。建议首次最大并发 1、GPU 独占，先验证环境和资源消耗；显存需求依据实际环境测量设置。

模板 endpoint_exclusive=true 时，平台将同主机、端口的推理服务视为单连接资源，即使不同用户/集合也不会重叠执行。未确认清理的任务继续持有端点。服务地址应统一使用同一个规范名称；DNS 别名/IP 别名无法自动识别为同一服务。并行吞吐需要独立服务实例/端口，不能仅增加集合并发。

客户端参数白名单：task_config、control_mode、state_source、eef_config、max_steps、instruction、instruction_type、video_fps。任务/config/mode 均为单值，平台负责拆分集合。任务参数不能修改 Cosmos/RoboTwin 根目录或宿主机路径。

## 逐回合结果与跳过 seed

SDK 的 episode_id 是平台计划回合。上游每次调用 episodes=1，自身可能跳过 unstable/expert-failed seed。平台只在 metrics.json 有且仅有一个正常完成回合时上报 COMPLETED；success=false 是有效评测失败，不是进程异常。

每回合显示 requested_seed、actual_seed、skipped_seeds、executed_steps、policy_seconds。下一次调用从上个 actual_seed+1 开始，避免重复仿真。平台 plan_seed 仍保留确定的计划编号对应种子，但不冒充实际执行 seed；实际值以指标中的 actual_seed 为准。失败尝试保留本地上游记录，不计有效完成回合。

WebSocket 建连/接收受平台 request_timeout_seconds 约束，默认 10 秒可能不足以等待真实模型推理，应按模型延迟设置（平台允许 1–120 秒）；整项还受 timeout_seconds 约束。长初始化、跳过不稳定 seed 或长推理均受到总超时控制。

## 输出

平台上传 `sample-report.json`，包含每回合结果、实际 seed 和本地输出位置。上游视频、动作数组、requests.json、metrics.json 留在 Worker：

```text
workspace_root/runs/<execution_id>/work/
  episode-000001/<task>/<config>/<mode>/...
  episode-000002/<task>/<config>/<mode>/...
```

完整视频不自动上传，避免超过平台单文件 8 MiB/总量 32 MiB 限制；本地数据遵守 Worker 保留和磁盘准入规则。当前不支持跳过已完成回合续跑，重试会创建新执行目录。

## 验证记录（2026-10-29）

平台后端回归 55 项通过；3 项需独立 PostgreSQL 的集成用例在本轮默认测试中跳过，前期独立数据库验证已通过。此后新增的资产原子发布检查已验证；随后按用户要求移除了客户端启动时的全量资产检查。前端类型检查和生产构建通过。Cosmos 固定版本自带模拟环境测试 50 项通过。新增迁移 b285c07 已在隔离 PostgreSQL 检查并部署 R8000 API，OpenSpec 严格校验通过。

不含大资产的仿真镜像已在 R8000 构建并通过依赖预检、客户端入口检查、无资产基础渲染和真实任务初始化自检。R8000 Worker 已登记该镜像；全量资产仍在宿主机后台下载。真实推理服务及 EEF 配置尚未提供，未运行真实模型闭环。

R8000 当前后台日志：资产 `/home/huawei/mini-eval-workspaces/r8000/cache/robotwin/download.log`；镜像 `/home/huawei/cosmos-robotwin-build.log`，状态 `/home/huawei/cosmos-robotwin-build.status`。此前基础镜像下载与 Git 克隆遇到网络中断；现已修复构建，状态为 `image-verified-assets-pending`、`exit_code=0`。

资产解耦验证：11 项样本测试通过，包含无 RoboTwin 资产 manifest 的客户端 setup 和独立 render-only 检查。R8000 使用已有 `mini-eval-runner:robotwin-render` 镜像挂载样本代码，未挂载 RoboTwin 资产，实际运行 render-only 成功：image_std≈0.15185，box_height≈0.10000。这验证了无资产基础渲染，不代表完整 Cosmos 镜像已构建成功或真实任务已完成。

R8000 当前使用显式 NVIDIA runtime，手动运行时将 `--gpus all` 替换为 `--runtime nvidia -e NVIDIA_VISIBLE_DEVICES=<GPU UUID>`；平台 Worker 已采用此方式。


平台已注册完整任务集合：ID `6bc93f01-08f0-44f0-99a3-bad73370e631`，模板 ID `121e2442-e28d-4572-8c54-673686ec3b95`。通过 `GET /api/v1/suites` 可查到该集合；它有 50 项，每项 100 回合。客户端镜像名为 `mini-eval-cosmos-robotwin:2ba852c-platform1`。

发起集合运行时提交 `suite_id`、目标 `pool_id`、`max_concurrent` 和公共 `client` 设置（模板、WebSocket endpoint、超时、显存/GPU 请求）。平台将集合 50 项变成 50 个 Job。调度器逐 Job 找同一 pool 中可用的 worker slot，要求 Worker 在线、支持协议版本 3、在 `client_images` 中声明了该镜像，并满足 GPU 型号、显存、CPU/RAM 和磁盘条件。符合条件后将 Job/执行 ID/冻结的 `client_spec` 分配给一个 Worker；Worker 启动该镜像中的 `runner.sdk`，把该 Job 配置写入容器，客户端读取 `task_name`、seed 和该项的 `parameters`。任务成功释放资源后，再派发下一项；未匹配的 Job 留在队列。

**R8000 已具备客户端镜像调度能力，多 Worker 并行尚未验收。** 平台数据库已确认 R8000 的 `client_images` 包含 Cosmos 镜像，协议版本为 3；本机 RTX 4090 尚未部署此镜像。Cosmos 模板标记 endpoint-exclusive，同一 endpoint 同一时刻最多一个 Job。保留端点独占时，同端点集合只能串行；要并行，需要多个推理端点并让每项选择端点，或验证服务端支持多会话后再放开互斥。真实运行仍需推理地址、匹配的 EEF 配置和该任务所用资产。

### 构建恢复记录（2026-10-29 晚）

PyTorch3D 官方 0.7.8 的标签为 `V0.7.8`（大写 V）。镜像改为使用该版本 commit `75ebeeaea0908c5527e7b1e305fbc7681382db47` 的源码归档，并校验 SHA256 `09378f154554ccdf02a8a0373ff715edb44ddfc3814d2cfdc308f4f49af67ab9`，避免 Git 克隆历史时的 TLS 中断。`PYTORCH3D_SOURCE_URL` 可指向管理员维护的同一归档缓存；校验值不随 URL 覆盖改变。

R8000 构建使用 `http://127.0.0.1:18089/pytorch3d-75ebeea.tar.gz` 本机缓存。下载代理通过本机到 R8000 的 SSH 隧道提供，独立监护进程在隧道退出后自动重连；代理自身和本机网络仍需可用。背景纹理采用 HTTP/1.1 断点续传。

### R8000 镜像与 GPU 验证结果

- 镜像：`mini-eval-cosmos-robotwin:2ba852c-platform1`，image ID `sha256:fdcee491d11f33dff8175a2a206e187a7193fb24e8d889ed0ca4fdd787c40240`。
- 构建后 `preflight --environment-only` 和上游客户端 `--help` 通过；无资产 `--render-only` 通过，image_std≈0.15185，box_height≈0.10000。
- `adjust_bottle --seed 100000` 被上游判为物体不稳定，未计为成功；`--seed 100001` 成功初始化，head/left/right 三路相机均为 `[240,320,3]`，GPU 为 Quadro RTX 8000。
- 自检使用分别校验过 SHA256 的机器人、物体归档副本：`/home/huawei/mini-eval-build-cache/smoke-assets`，只读挂载到 `/opt/mini-eval/assets`。此目录没有全量 manifest，背景纹理继续下载。该快照用于验证 `demo_clean`，没有替换 Worker 的标准 assets 挂载。
- 日志：`/home/huawei/cosmos-render-check.log`、`/home/huawei/cosmos-task-check.log`（不稳定 seed）、`/home/huawei/cosmos-task-check-seed100001.log`（成功）。
- R8000 Worker 已重启，上报镜像名单包含此镜像，平台数据库读回确认。样本 11 项回归测试通过。全量资产尚未发布，真实模型闭环未执行。

## 交付复核（2026-10-31，替代上述旧状态）

- 两机已有相同镜像 `mini-eval-cosmos-robotwin:2ba852c-platform1`，镜像 ID 为 `sha256:fdcee491d11f33dff8175a2a206e187a7193fb24e8d889ed0ca4fdd787c40240`；平台 Worker 均报告该镜像可用。此前“本机尚未部署镜像”已过时。
- R8000 的全部三个资产归档已下载并发布至标准 `workspace_root/assets/robotwin/3dc3b798668feb99ac61cc9086d84cbcc3d79186`。复核 SHA256、20,841 个原始文件及 6 个容器路径配置通过；此前 `image-verified-assets-pending` 是历史构建状态，不代表当前资产状态。
- 两机固定版本镜像内均包含官方完整 50 项任务及集合所用配置，平台注册清单与仓库清单一致。此检查只验证任务定义和文件存在，不声称逐项跑过全部 50 个仿真场景。
- 本机当前另有 `assets/robotwin-queue-smoke` 的部分资产，完整归档正从 R8000 后台同步；下载、校验、解压完成后原子发布标准版本目录。未完成前不能把本机标为全量资产就绪；不要把部分资产改名或补写完整 manifest。
- 本轮仿真初始化自检只验证环境与相机输出，没有连接真实推理服务。真实模型评测仍需服务地址与匹配的 EEF 配置，即使 joint 模式也不应伪造坐标变换。

下载器现在支持主动复核已发布内容（只读，不作为任务准入条件）：

```bash
python -m examples.cosmos_robotwin.assets \
  --assets-root /absolute/worker/assets \
  --cache-dir /absolute/worker/cache/robotwin \
  --verify-only
```

它先验证固定归档 SHA256，再逐文件比较解压内容的 SHA256，并核对生成的机器人配置。缺失、损坏或符号链接替换均报错，不修复运行中的版本。这个命令会读取大文件，需要单独安排执行；客户端启动和调度不调用它。

本次本机一次性同步任务由本轮交付启动，不是定时任务，不会周期性执行。进度在项目 `.runtime/robotwin-delivery/local-sync-status.json`，日志在同目录 `local-sync.log`；阶段依次为 `transferring`、`verifying-and-publishing`、`auditing-published-files`、`complete`，失败为 `failed`。只有 `complete` 且 `local-assets.json` 验证通过，才代表本机完整资产交付完成。

详细验收和后台同步边界见 [交付复核记录](../../docs/robotwin-delivery-validation.md)。
