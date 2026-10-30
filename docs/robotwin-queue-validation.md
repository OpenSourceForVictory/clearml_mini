# RoboTwin 双机排队验证

这套集合用于验证真实仿真场景启动与平台调度，不连接模型推理服务。界面中的成功表示场景初始化和三路相机检查通过，不表示机器人策略完成任务。

## 集合与客户端

- 集合：`RoboTwin 三任务场景自检（无模型）`，版本 `1`。
- 任务：`adjust_bottle`、`beat_block_hammer`、`click_bell`，每项 1 回合，初始 seed=100001。
- 客户端：`examples.cosmos_robotwin.queue_client:RobotwinEnvironmentClient`。
- 镜像：`mini-eval-robotwin-queue-check:1`，构建文件 `examples/cosmos_robotwin/Dockerfile.queue-check`。
- 每项产生 `environment-episode-1.json`，包含实际 seed、跳过的不稳定 seed、相机尺寸和 `model_evaluation=false`。底层日志保存在 Worker 执行目录的 work 中。
- 仅上游明确的 `UnStableError` 可重试，最多三个 seed；缺失资产、超时、异常退出或缺少结果报告都视为失败。

镜像使用 `workspace_root/assets/robotwin-queue-smoke/3dc3b798668feb99ac61cc9086d84cbcc3d79186` 的只读资产快照。三个任务只需要 aloha-agilex 机器人及 `001_bottle`、`020_hammer`、`050_bell` 物体，以及共享索引 `objects/objaverse/list.json`、`objects/same.json`；不需要随机背景纹理。该快照与后台下载的完整 RoboTwin 资产分开，不伪造完整 manifest。

## 提交多个批次

两台 Worker 必须在同一资源池，已装镜像且在 `client_images` 中登记，并重启完成能力上报。平台现有 CS 契约要求 endpoint；此样本填平台 HTTP 地址，仅通过入口的 TCP 连通性检查，客户端不向它发推理请求。模板的端点独占关闭；真实 Cosmos 模板的独占设置不变。

运行脚本通过环境变量读取 `EVAL_ADMIN_PASSWORD`，可选 `EVAL_ADMIN_USERNAME`（默认 admin）、`EVAL_API_URL`、`QUEUE_POOL_NAME`（默认 gpu-pool）。

```bash
# 先在当前终端设置 EVAL_ADMIN_PASSWORD，避免把密码写入脚本。
python3 scripts/robotwin_queue_check.py
```

脚本注册三任务集合，连续提交 3 个批次，每批最大并发 2，每任务 GPU 独占、请求 8 GiB 显存，超时 900 秒。它每 5 秒记录状态、执行节点、开始/结束时间，并在所有任务终止后保存逐回合结果；存在失败或超时则返回非零退出码。所有任务成功后仍需核对下述并发与排队证据。

状态及证据默认保存到 `.runtime/robotwin-queue-exercise.json`。使用同一文件重新运行只恢复监测，不重复提交已有批次；另起一轮时通过 `QUEUE_STATE_FILE` 指定新的文件路径。`QUEUE_REGISTER_ONLY=1` 仅注册，不提交。

## 验收标准

- 两台不同型号 GPU 主机都有实际执行记录，并且运行时间区间有重叠。
- 9 项工作超过两张独占 GPU 的容量时存在 PENDING 任务。
- 资源释放后，先前排队的工作自动开始，无需人工分配。
- 逐回合结果和产物可查看，记录每个任务的最终状态与实际 seed。

## 首轮现场记录（2026-10-29）

集合 ID：`e5bef998-52c5-4339-93de-b33b2dbc95b2`。提交 3 批、共 9 项。实际峰值并发 2，初始 9 项排队，8 项先排队后进入 RUNNING，另 1 项在启动阶段超时。两台主机实际运行时间有重叠，队列在任务释放资源后自动接续。

首轮结果为 **3 成功、5 失败、1 超时，尚未通过完整验收**。R8000 成功 3 项，本机 6 项均未成功。初期本机缺少共享资产索引，已补齐；另有 native SIGILL 记录。暂停本机节点期间有 1 项启动超时，随后已恢复节点及 Worker。

后续独立复测发现本机仿真被外部 SIGKILL 终止；8 GiB 容器限制下实际约 1.5 GiB 占用，Docker `OOMKilled=false`。内核跟踪确认 root 定时任务 `/var/tmp/6cbba609/placi` 派生 `/usr/bin/kill`，向仿真的 `pt_main_thread` 发出信号 9。这是首轮复测的阻碍；随后按用户授权清理了两台机器的已知异常程序，并在下述第二轮重新验证。

| 批次 | 任务 | 节点 | 首轮状态 |
| --- | --- | --- | --- |
| 1 | `adjust_bottle` | r8000 | SUCCEEDED |
| 1 | `beat_block_hammer` | local-4090 | FAILED |
| 1 | `click_bell` | local-4090 | TIMED_OUT |
| 2 | `adjust_bottle` | local-4090 | FAILED |
| 2 | `beat_block_hammer` | local-4090 | FAILED |
| 2 | `click_bell` | r8000 | SUCCEEDED |
| 3 | `adjust_bottle` | local-4090 | FAILED |
| 3 | `beat_block_hammer` | local-4090 | FAILED |
| 3 | `click_bell` | r8000 | SUCCEEDED |

批次 ID：

- `2dbdd944-407b-4ab8-82aa-817ad75a5256`
- `63230c38-9fff-4f5a-9e10-75182a8f7de8`
- `5e1b2e1b-a737-4134-ab02-bd56e71d61a3`

原始状态和逐回合证据保存在 `.runtime/queue-initial/robotwin-queue-exercise.json`，汇总在 `.runtime/robotwin-queue-summary.json`，信号调用链在 `.runtime/robotwin-signal-origin.txt`。相关 15 项单元测试通过，但不能替代待完成的现场验收。

## 第二轮现场验收（2026-10-30，通过）

北京时间约 15:42–15:45，重新提交三个批次，首轮失败记录保留不覆盖。9 项均成功，本机 RTX 4090 执行 5 项，R8000 执行 4 项。两机实际运行区间有重叠；轮询与开始/结束时间分别验证最高并发均为 2。最初 9 项排队，两项运行时另有 7 项等待，全部排队项随后自动启动。9 个回合均报告成功，`environment_check=1`、`model_evaluation=0`；本轮所有任务使用 seed=100001，没有不稳定 seed 重试。

| 批次 | adjust_bottle | beat_block_hammer | click_bell | 结果 |
| --- | --- | --- | --- | --- |
| 1 | local-4090 | r8000 | local-4090 | 3/3 成功 |
| 2 | r8000 | local-4090 | r8000 | 3/3 成功 |
| 3 | local-4090 | r8000 | local-4090 | 3/3 成功 |

批次 ID：

- `75cef6e6-f421-4ce8-bbd1-0219169d8824`
- `de38b75f-6dce-4e6c-97a8-0cea80451e42`
- `0ca52670-1028-44ac-a781-cb0480136ad0`

可审阅证据：[验收摘要、逐任务时间与指标](validation/robotwin-queue-20261030.json)。完整 API 时间线及逐回合报告保存在 `.runtime/robotwin-queue-rerun-20261030.json`，执行日志在同名 `.log` 文件；原始文件不进入 Git。

复核命令：

```bash
python3 scripts/verify_robotwin_queue.py \
  .runtime/robotwin-queue-rerun-20261030.json \
  --output .runtime/robotwin-queue-rerun-20261030-summary.json
```

验证脚本同时检查 9 项成功、两台已知节点时间重叠、实际并发上限、排队接续和 9 份真实场景结果，失败返回非零。相关客户端、Cosmos 和证据校验测试共 17 项通过。

本次完成的是无模型场景自检和双机调度验收，不代表策略成功率或真实推理闭环已验收。完整背景纹理此前因网络超时中断，已重新启动后台断点下载，完整资产发布仍需单独确认。安全事件清理后的测试通过，也不代表系统已经恢复可信。
