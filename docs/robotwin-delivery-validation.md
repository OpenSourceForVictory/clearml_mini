# RoboTwin 镜像与资产交付复核（2026-10-31）

本轮收尾 `cosmos-robotwin-sample` 的 2.3：R8000 固定版本镜像、完整资产下载发布、只读挂载与内容校验。真实模型闭环仍不在本轮范围。

## 已确认

- 两台 Worker 已安装并登记相同 Cosmos 镜像：`mini-eval-cosmos-robotwin:2ba852c-platform1`，镜像 ID `sha256:fdcee491d11f33dff8175a2a206e187a7193fb24e8d889ed0ca4fdd787c40240`。
- 两机镜像内依赖导入、CUDA 扩展导入、ffmpeg 编码器及上游入口检查通过；后续 GPU 场景自检验证实际 CUDA 与 SAPIEN 工作。
- Cosmos commit `2ba852c1ff3ce923405bd47a65074488a3358781`，RoboTwin commit `ea8b21121ebb3cd201ff5b3fe361944ac94eda3f`。两机 50 项清单与镜像内官方 `env_cfg/eval/all_tasks.yml` 一致，任务 Python 文件和所用 `demo_clean.yml` 均存在；平台注册清单与仓库一致。
- R8000 的背景纹理归档已于 10 月 30 日下载完成，三个归档的 SHA256 均匹配固定值。标准版本目录存在有效 manifest；主动复核 20,841 个原始文件及 6 个生成配置通过。
- R8000 的标准完整资产以只读挂载通过 `adjust_bottle`、`beat_block_hammer` 的真实 GPU 初始化自检；本机同两项使用已有部分资产通过。三路相机均为 240×320 RGB，seed=100001。

新增 `assets --verify-only` 只读检查已发布内容，可检测文件损坏、缺失、符号链接替换和生成配置错误。相关 Cosmos/队列客户端/证据测试共 18 项通过。该命令是交付工具，不加入 Worker 调度或客户端启动准入。

## 本机后台同步

本机已有镜像与三任务自检所需部分资产，完整资产尚在同步，**不标记全量资产就绪**。本次一次性任务从 R8000 复制固定归档，按顺序校验、解压、原子发布，再逐文件复核；成功状态为 `complete`，失败保存错误，不改动旧版或部分资产目录。

任务脚本：项目 `.runtime/robotwin-delivery/sync_local.py`；状态：`local-sync-status.json`；日志：`local-sync.log`；PID：`local-sync.pid`。这是本轮明确启动的后台任务，不是 cron 或自动定时程序。它不修改 Worker、资源池或平台配置，也不在后台自动启动 GPU 测试。

完整资产目标：`/home/dgh/mini-eval-workspaces/local4090/assets/robotwin/3dc3b798668feb99ac61cc9086d84cbcc3d79186`。本机完整目录发布前，不应据此声称可运行所有真实仿真任务。

## 边界与交付

完整清单文件检查不等于遍历 50 项场景。没有推理服务地址或匹配的 EEF 配置，不声称真实模型评测成功。

Cosmos 固定镜像内旧自检 CLI 仅列出两个任务；`click_bell` 使用基于相同环境构建的既有 `mini-eval-robotwin-queue-check:1` 自检镜像另行检查，两机均通过，seed=100001、三路 RGB 均为 240×320。完整 Cosmos 客户端按上游任务名执行，不由这个自检 CLI 的 choices 限制。

本轮只更新宿主机交付工具、测试和文档，无 API/UI/数据库改动，无需重启服务或 Worker。R8000 原工具备份为 `/home/huawei/clearml_mini/.runtime/robotwin-assets-before-delivery.py`；可恢复该文件回退工具，已发布资产不删除、不覆盖。

结构化证据见 [交付记录](validation/robotwin-delivery-20261031.json)。详细日志保存在 `.runtime/robotwin-delivery/`。一次性 GPU 检查容器以 `--rm` 运行，资产同步任务按用户之前允许的后台方式保留运行。
