# 宿主机运行发布验证（2026-11-18）

控制端镜像 `mini-eval-api:host-runtime-heartbeat-20261118` 已在保留 LAN 端口覆盖配置的前提下部署，API 健康检查通过；数据库版本与镜像迁移头一致，发布前 `pg_dump` 备份已验证可读取。Worker 不可变发布摘要为 `aba0b49ff95f3a10989371808219ef2d6e81b47c43d0295fef1403e68c14c0a6`。六台已纳管机器的升级状态均为 `SUCCEEDED`，13 个设备实例全部回连并报告同一摘要。

宿主机真机验证：

- 本机 RTX 4090 客户端使用平台任务和批准的 Anaconda Python 执行 CUDA 张量平方求和，日志记录 `GPU_SMOKE NVIDIA GeForce RTX 4090 GPU-1568a9ba-cc52-a2a1-5771-d76bf6ea176f 357389824.0`；任务 `SUCCEEDED`，清理已确认。
- 29 号 Ascend 910B3 服务端使用平台服务实例与 `HwHiAiUser`（UID 1001）启动。ACL 设备绑定/内存申请的服务达到 `HEALTHY`；随后用节点批准的 Python 3.12、CANN 环境与 2 核/8 GB 申请运行 `torch_npu` 张量平方求和，日志记录 `NPU_TORCH_SMOKE 0 1001 1240`，服务达到 `HEALTHY`。测试服务已停止，执行清理已确认；临时宿主机环境配置已恢复，测试模板已停用。

保留的失败证据：首次使用 `/usr/bin/python3` 的 NPU 环境探测因版本低于 3.10 失败；使用公网 SSH 地址部署服务因地址不在节点本地失败；`HwHiAiUser` 对缓存目录无写权限，补充该账号 ACL 后通过；1 GB 内存申请的 `torch_npu` 服务未完成启动，改用 8 GB 后通过。第一次 ACL 服务运行时曾因控制端执行心跳短时返回 HTTP 503 中断，触发“previously created host unit is missing”后续报错；`ce273f0` 修复为 503 保留现有单元并重试，相关定向测试 60 通过、3 项需真实 systemd 的自动化用例跳过。没有运行用户此前要求暂缓的全量测试或全量覆盖率统计，不能据此宣称覆盖率门槛已达到。

本验证只覆盖 29 号节点的 `/mnt/sfs_turbo/lianjie/runtimes/lingbot-cloud` 环境，不覆盖另一台机器的 `/efs-mi-east4-1/apps/miniconda3/envs/causal-wam`；后者的 `No module named 'tbe'` 仍需在其业务身份下核对 CANN `set_env.sh` 与 Python 模块搜索路径。owner mismatch 警告在 29 号节点也出现，但未阻止 `torch_npu` 运算，不应单凭该警告推断崩溃原因。验证程序只是设备计算冒烟，不是实际模型评测。
