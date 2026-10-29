# Cosmos / RoboTwin 客户端样本

这是平台 SDK 的具体接入样本，定义了评测任务集合和客户端实现。**本次不需要提供服务端地址，也未运行真实模型闭环评测。** 运行时再填写推理服务地址。

上游：[causal_action_server.md](https://github.com/bevictory/cosmos-framework/blob/feat/robotwin-client-server/docs/causal_action_server.md)。固定版本：`2ba852c1ff3ce923405bd47a65074488a3358781`，不复制上游代码；镜像构建保留其仓库及许可证。

## 文件与用途

- `suite.json`：可直接在平台导入的清单，adjust_bottle 和 beat_block_hammer 各 2 回合，demo_clean/joint，起始 seed=100000，max_steps=32。
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

下载支持断点续传，压缩包保留在 cache；校验并解压至隐藏临时目录后，原子发布 `assets/robotwin/3dc3b798668feb99ac61cc9086d84cbcc3d79186/manifest.json`。已有版本不覆盖、不自动删除。没有 manifest 时预检失败，不把未完成下载当作可用资产。模板中的机器人路径写为容器路径 `/opt/RoboTwin`，不会绑定某台宿主机的绝对路径。

镜像构建和 `preflight --environment-only` 不需要挂载资产；GPU 仿真及正式任务必须挂载就绪版本。手动 `docker run` 使用上面的 `--mount`，平台 Worker 已自动挂载其 assets 目录，无需每次提交任务额外指定宿主机路径。

将匹配训练坐标系的 EEF JSON 放入各 Worker 的 `workspace_root/assets/cosmos/eef.json`。上游代码强制要求 EEF 配置或数据集坐标元信息，即便使用 joint 控制也会发送 endpose。**不能猜测 T_world_base 或用单位变换冒充真实配置。** world/base 的选择必须与训练数据一致；配置格式由上游 RobotwinPoseConverter 校验。

```bash
docker run --rm \
  --mount type=bind,source=/absolute/worker/assets,target=/opt/mini-eval/assets,readonly \
  mini-eval-cosmos-robotwin:2ba852c-platform1 \
  python -m examples.cosmos_robotwin.preflight
```

管理员将镜像分发到执行节点并加入 Worker 的 client_images。确认依赖就绪后才登记 template.json；镜像构建和 GPU 自检未通过时，不应将样本标成可在线执行。

## 定义集合与发起运行

在平台选择登记后的 Cosmos 模板，导入 suite.json，保存集合名称和版本。这一步不需要推理服务地址。以后发起运行时填写 `ws://模型机器:端口`、资源池和资源请求。建议首次最大并发 1、GPU 独占，先验证环境和资源消耗；显存需求依据实际环境测量设置。

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

平台后端回归 55 项通过；3 项需独立 PostgreSQL 的集成用例在本轮默认测试中跳过，前期独立数据库验证已通过。此后新增的资产原子发布和未就绪拒绝检查在 8 项样本专项测试中通过。前端类型检查和生产构建通过。Cosmos 固定版本自带模拟环境测试 50 项通过。新增迁移 b285c07 已在隔离 PostgreSQL 检查并部署 R8000 API，OpenSpec 严格校验通过。

不含大资产的仿真镜像正在 R8000 构建；资产在宿主机后台下载。尚未完成 GPU 任务初始化验收，因此模板暂不登记为在线可执行。真实推理服务及 EEF 配置尚未提供，未运行真实模型闭环。

R8000 当前后台日志：资产 `/home/huawei/mini-eval-workspaces/r8000/cache/robotwin/download.log`；镜像 `/home/huawei/cosmos-robotwin-build.log`，状态 `/home/huawei/cosmos-robotwin-build.status`。基础镜像拉取遇到直连/TLS 超时，已保留下载与后续构建检查流程；在状态到达 `image-verified-assets-pending` 前，不视为镜像构建成功。
