# 宿主机登录环境与逐回合启动验证（2026-11-18）

OpenSpec：`host-shell-episode-launch`。本次在仓库代码上运行定向测试；没有运行 `python -m scripts.test_coverage` 全量门禁，不能据此宣称达到项目 80/70、关键功能 90/80 覆盖率门槛。前端没有行/分支覆盖率插桩。

- `test_host_shell_episode.py`：18 通过，包含登录环境成功/超时/无效输出、设备变量覆盖、direct/普通命令/SDK 逐回合、业务参数、失败停止、Docker 原行为及 API 完成门槛。
- 宿主机、普通命令、设备边界定向插桩：83 通过、27 跳过。跳过的是需显式开启的现场/systemd 等测试；父进程覆盖率未合并业务子进程，`runner/host_episode.py` 等在该局部报告中显示 0%，不能作为真实全项目覆盖率。原始局部报告在本地 `/tmp/mini-eval-host-targeted.json`，不纳入发布制品。
- SDK 下载包、宿主机工作目录、runner 入口与模板版本：首次 1 失败（代码改变后下载 ZIP 未重建），同步加入 `host_episode.py` 并重建 ZIP 后，42 通过、4 跳过。前端宿主机模板保存/复制 2 通过，TypeScript 与 Vite 构建通过；Vite 提示现有大于 500 KiB 的 chunk。
- 29 号 NPU 节点：在无活动执行单元时，将当前 runner 代码暂存于独立临时目录，以节点批准的 `HwHiAiUser` 读取非交互登录 shell 导出变量（仅记录存在性），再使用短小前台程序执行 direct、普通命令和 SDK 命令，每种 2 回合均得到 `STARTED, COMPLETED, STARTED, COMPLETED`、退出码 0。程序确认看到节点登录环境的 `ASCEND_HOME_PATH` 和预期 `ASCEND_RT_VISIBLE_DEVICES=0`。临时目录已删除。首次测试因暂存目录权限拒绝导入 runner，调整仅暂存代码目录的读取权限后通过；SDK 样例的输入缺少 endpoint、又误以为旧 SDK 输入为单回合文件，修正测试输入后通过。这些是测试布置问题，原始失败已保留在会话输出。

发布前数据库备份保存在忽略 Git 的 `.runtime/host-shell-episode-release-20261118/before.dump`（0600）；镜像 `mini-eval-api:host-shell-episode-20261118` 使用原 Compose + LAN 覆盖配置上线。数据库与镜像的 Alembic head 都是 `w396f78`，无需迁移。API `/healthz` 返回 200。Worker 发布摘要 `5a2e4fbd5431a5bb61132a4ec0c2b544ea7b67e3742df2006edb77cb2411bc45`，仅升级 29 号节点的 8 个 NPU Worker 和本机 1 个 GPU Worker，均报告 0.18.0 / 协议 15、升级 SUCCEEDED；其他节点保留原版本，可继续运行旧模板，新字段按协议门槛拒绝调度到它们。

平台端到端 GPU 验证：普通命令宿主机模板选择登录环境及逐回合，任务 `6f52d98f-aa4f-46d2-a5b3-bdaf6a15ec61` 分配到本机 RTX 4090，执行 `51e5eba1-86e1-4b3b-ae6a-587f7f518a25`。业务程序每回合只看到指定 UUID，并实际完成 CUDA 张量平方运算；两条回合记录为 COMPLETED，任务 SUCCEEDED、清理已确认，结果指标为 `None`，没有伪造评测成功率。首次用 direct 评测模板、无服务提交被 API 以“evaluation templates require an inference service”拒绝；改为符合协议的普通命令模板后通过。任务曾在 PENDING 停留，手工调用一次现有调度 tick 后成功领取。随后以 `/bin/true` 提交第二个最小任务 `695b9d47-becf-4653-ba8e-11c013f624d9`，在观察的 22 秒内仍处于 PENDING；同时间调度心跳持续更新，人工逐项检查节点、模板、资源均无阻塞码，再手工调用一次 tick 才领取并成功、清理确认。这表明自动领取时延仍有未定位问题，不能把这两次手工触发的成功视为自动调度验收。两次测试模板已归档，临时程序已删除。

平台端到端 NPU 验证：29 号节点在现有批准环境下以 `HwHiAiUser` 和模板 `environment_source=login` 启动宿主机服务，分配 NPU 2。服务 `878093ff-c59f-487b-a501-d325b9e02641`、任务 `c340d4ca-56cf-4f36-b7ee-9de38921d219` 的日志包含 `NPU_LOGIN_SERVICE_READY`，实际 `torch_npu` 张量计算通过，健康状态到达 HEALTHY。随后通过平台停止，状态 STOPPED、`cleanup_pending=false`；模板已归档、节点临时程序已删除。该节点现有服务网络配置的公告地址与节点真实本地地址不同，本次仅验证节点本地健康探测，未宣称跨节点客户端可连接。

真实节点程序不是模型评测；GPU 可见 UUID 不构成硬隔离。NPU 逐回合 direct/普通命令/SDK 的业务进程链路使用临时程序验证，但未在 NPU 服务端角色节点通过 API 提交客户端逐回合任务；GPU 端到端只覆盖普通命令模式。完整三模式 API 端到端与全量覆盖率仍未验收。

## 宿主机范围完整复测（2026-11-18，追加）

本轮按用户要求扩大宿主机业务执行测试范围，不运行全仓库测试。没有修改业务实现或再次部署。

- 宿主机 backend/lifecycle/environments/working-directory/managed-host/supervisor-output、shell/episode、parameter-contract、command-sdk/generic/command-tasks、SDK reporting、Worker service/execution recovery、service readiness 与 launch boundaries：242 passed / 70 skipped。
- 显式开启 `HOST_EXECUTION_TEST=1` 后，`test_host_backend.py` 与 `test_host_working_directory.py`：28 passed（含上组跳过的 7 项，不能与上组简单相加）。服务 recovery/readiness execution 仅选择 host：17 passed / 17 deselected，使用真实 systemd 进程、模拟 API 边界，不是平台端到端。
- `test_execution.py`、`test_services.py`、`test_service_readiness.py`、`test_accelerator_resources.py`：37 passed；`test_npu_cann_environment.py` 与 `test_task_input.py`：27 passed。一次测试命令误用了不存在的 `test_npu_host.py`，pytest 返回 4、没有运行测试，随后修正列表。
- 仍未开启独立 root 测试账号的 5 项 managed-host 用例、24 项 Docker generic matrix 以及服务 Docker 对照项；不能声称所有现场矩阵通过。已有 Docker 兼容单元回归通过不等于本轮完整 Docker 真机验收。

真实 GPU runner 矩阵：以当前批准账号读取真实登录 shell 导出环境，将其传入业务子进程；direct、普通命令、sdk-command 每种 2 回合，均执行真实 CUDA 张量平方计算，并断言 HOME、模板业务变量、指定 GPU UUID 和业务 argv 映射。三种模式全部通过。该检查使用独立临时输入与 runner 入口，不经过平台调度或 Worker systemd，不冒充平台端到端。单元测试另覆盖参数 JSON、seed、业务返回缺失/非法/非零退出、设备变量优先级、登录环境超时及非法输出。

平台 NPU 服务：`373966d6-b775-4e0e-b965-5ebfffd96176`，任务 `2a0468eb-ee78-4c64-8c69-b7fe2a16329e`，通过自动调度启动。日志 `HOST_NPU_VERIFIED uid=1001 visible=0 tensor=ok env=ok`，确认批准业务账号 HwHiAiUser、登录 CANN 环境、模板变量和真实 torch_npu 张量运算；HEALTHY 后通过平台停止，STOPPED 且 cleanup_pending=false。当前实现只允许访问获分配的物理卡，业务使用逻辑卡 0；本轮未额外抓取 systemd DeviceAllow 作为物理卡隔离的新证据。

保留两次 NPU 测试布置失败：任务 `ce8e4fb9-1a0e-42c6-a478-8c5234561292` 在启动前因新上传脚本不可读被正确拒绝，只修正该临时文件为 0644；任务 `c206b986-d6ca-4f04-a799-968f1c980023` 因测试把 ASCEND_RT_VISIBLE_DEVICES 错断言为物理编号 2 而失败，修正为实现使用的逻辑编号 0 后通过。未修改业务账号、CANN 安装或全局权限。

平台 GPU 客户端未通过：第一次任务 `2e3e4478-a811-4e7a-9f85-b07cc9eab0ac` 漏填 `command_mode=exec`，绝对 Python 路径被旧脚本模式的 approved-code-root 检查拒绝。修正测试模板后任务 `fdfb68cd-810f-4d1d-b062-e0d9a10f918d` 仍需手工 tick 才领取，随后首次 `worker/heartbeat`（started=false）收到 HTTP 503，在 client.py execute 初始握手处中断；最后 FAILED，错误为 `worker confirmed container stopped`，cleanup_confirmed=true。不能据此确认平台端到端业务参数传递成功。自动领取和首次握手 503 恢复是未解决问题。

所有本轮平台测试模板已归档，NPU 临时业务脚本已删除；结束时查询活动或未清理 evaluation_runs 为 0。

覆盖率补充：以 `backend/tests/project.coveragerc` 对 shell/episode、host lifecycle、parameter contract、CANN、task input 定向插桩，102 passed。局部原始报告 `/tmp/host-verify-coverage.json`；host_identity 行 48/75、分支 18/34，host_backend 行 169/223、分支 59/106，parameter_contract 行 148/151、分支 68/78。未合并业务子进程，host_episode 显示行 0/56、分支 0/14。该局部报告不能替代 `python -m scripts.test_coverage` 的全仓库目录/关键功能门禁，也未证明达到 80/70 或 90/80 要求。
