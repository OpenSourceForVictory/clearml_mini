# Design

## Context
上游 feat/robotwin-client-server 检查版本为 2ba852c1ff3ce923405bd47a65074488a3358781。现有平台 SDK 负责回合循环。上游以 WebSocket+msgpack/NumPy 交互，需要特定 RoboTwin 的 scripts/eval_policy_xpolicylab.py、仿真资产和 EEF 配置；官方 RoboTwin ea8b21121ebb3cd201ff5b3fe361944ac94eda3f 已包含该 helper，真实服务地址暂不处理。

## Goals / Non-Goals
**Goals:** 交付可运行接入样本并测试参数/结果映射、服务互斥；条件具备时执行真实模型验收。
**Non-Goals:** 不部署推理模型，不虚构 checkpoint/EEF 变换，不把模拟环境成功当模型成功。

## Decisions
- 适配器以 subprocess 调用固定上游入口，每个平台回合传 episodes=1，任务/config/mode 只允许单值；不复制上游源码。每次子进程使用唯一 output，避免上游 info.json 缓存错误复用。
- 上游自身负责跳过 unstable/expert seed；平台 SDK seed 是本回合请求种子，结果额外记录 actual_seed，适配器内部 next_seed 接着上个实际 seed。保存请求与实际值，保证同次运行不会重复评测跳过后的 seed。
- env COSMOS_ROOT 和 ROBOTWIN_ROOT 是镜像/管理员配置，EEF 配置只接受 assets 下相对路径。task_config/control_mode 等参数白名单，命令 argv 传递不用 shell。客户端调用单任务进程，整任务由现有 supervisor 约束；WebSocket recv 超时通过轻量启动包装器为上游连接设置超时。
- 产物摘要汇总到一个 sample-report.json；视频与 npy 保留 context.work_dir，避免突破 8 文件/32 MiB 限额。报告中明确保留路径。
- ClientTemplate 新增 endpoint_exclusive 默认 false，已提交快照保留值。调度器按规范化 URL 比较所有持有资源执行；任一方独占则互斥。DNS 别名无法自动识别，用户须使用统一服务地址。
- 样本 Dockerfile 从 CUDA/PyTorch 开发镜像构建 Python 3.10/3.11、RoboTwin 固定 commit、SAPIEN 3.0.0b1、cuRobo v0.7.8 ，并复制平台 SDK 与 Cosmos checkout。固定 NumPy 1.26.4 避免原生扩展 ABI 冲突；编译支持 RTX 8000 与 RTX 4090。构建不启动模型，不依赖宿主机 Python。大资产存放在 workspace_root/assets/robotwin/<固定 revision>，通过既有 assets 只读挂载和镜像内符号链接供 RoboTwin 使用。下载支持续传，SHA256 校验后在临时目录解压、生成使用容器路径的配置，原子发布版本目录与 manifest；运行中资产版本不修改。镜像构建的环境预检不要求资产就绪，资产在后台下载，完成后另行执行 GPU 自检。真实端点与 EEF 配置到位后再验收模型闭环。

## Risks / Trade-offs
- [上游文档与代码存在版本差异] → 固定 commit 并以参数解析和测试为准。
- [跨进程每回合初始化开销] → 第一版优先复用上游原始入口与可定位结果，后续可优化。
- [大视频无法从平台下载] → 留存在执行工作目录，平台提供摘要说明，不静默删除。
- [未提供真实环境] → 接入样本与真实模型验收分别记录。

## 用户确认的验收边界
用户明确先不处理服务端地址，本次交付评测集合定义和客户端实现；真实模型闭环不作为本次完成条件，不登记缺乏依赖的模板为在线可执行样本。

用户进一步确认：先交付镜像和客户端流程，资产下载在后台进行，资产完成后的 GPU 仿真验收单独跟踪，不阻塞当前阶段。

集合定义与文档对齐：保留 2 项轻量 smoke 子集，补充文档 2 task × 2 config × 2 mode、每项 3 回合的完整 8 项示例矩阵，以及当前 joint-only checkpoint 可用的 4 项子集；不将示例矩阵称为 RoboTwin 全量 benchmark。平台将各组合显式拆成子任务。

资产不是平台流程的强依赖：移除客户端 setup、配置预检和 GPU 自检入口的全量 manifest 校验。下载器仍校验并原子发布完整版本。新增 render-only 模式先验证 SAPIEN，不读取 RoboTwin 资产；真实任务的必要文件由仿真代码在使用时检查，不伪造成功。

构建恢复：PyTorch3D 固定官方 `V0.7.8` 对应 commit `75ebeeaea0908c5527e7b1e305fbc7681382db47`，源码归档校验 SHA256，允许覆盖归档下载地址以使用本机缓存。只读 GPU 自检可使用已分别校验机器人和物体归档的独立快照；不写入完整 manifest，不将其冒充全量资产发布，随机背景纹理继续后台下载。
