# NPU 独立日志平台完整链路（2026-11-19）

本次补齐前一轮未覆盖的 NPU 平台调度链路，并发现、修复普通业务账号独立日志采集缺陷。使用隔离 API/PostgreSQL、当前源码及 29（1.95.69.29）的临时 Worker，未升级生产平台或 `/etc/mini-eval-node` 下原有 8 个 Worker。测试服务为实际 ACL 显存分配/释放小程序，不是真实模型质量评测。

## 完整链路结果

- 同一台 29 的物理 NPU 6、7 注册到独立服务端资源池；本机真实 RTX 4090 Worker 作为客户端。
- 通过 `POST /orchestrations` 提交同一模板、同一个逻辑服务、`instance_count=2`。平台分配两个 slot、执行 ID 和端口（24100、24101），不是手工启动两个业务程序。
- systemd 以普通业务账号 `HwHiAiUser` 启动两个服务，分别仅允许 `/dev/davinci6`、`/dev/davinci7` 及公共管理设备；进程看到逻辑设备 0。每个服务实际执行 ACL 初始化、申请/释放 1 MiB 显存。
- 服务故意等待 8 秒再监听；Worker HTTP 就绪检查连续成功两次才判定 HEALTHY。观察到 STARTING 阶段客户端未运行，随后客户端使用平台注入的 endpoint 请求服务并成功完成。
- 两个 service scope 都经节点落盘、Worker 索引登记、API 请求邮箱读取正文；stdout、stderr、中文和 execution_id 均正确，不包含另一实例的执行标记。
- 真实 Chromium 打开批次“服务端与关联”：显示同节点 NPU 6、7，分别展开日志、过滤 stderr、下载 JSONL 并解析核验。无接口 mock。
- 通过平台停止两个实例，等待 STOPPED、cleanup_confirmed，核对两个 systemd unit 不再 active。重启这两个隔离 Worker 后，仍能通过新的 API 请求读取历史正文。
- 服务是由平台主动终止，采集没有正常收尾，历史 scope 如实为 `incomplete`；不是丢失正文，也不宣称最后输出完整。任务停止成功与日志完整性分开。

证据：[提交与就绪](submit.log)、[观察时序](readiness-observations.json)、[实例日志](service-evidence.json)、[systemd 用户及设备限制](systemd-evidence.json)、[读取结果](read_logs.log)、[浏览器操作](browser.log)、[双实例页面](services.png)、[停止与重启读取](stop_and_read.log)、[历史完整性状态](history-evidence.json)。业务小程序见 [server.py](server.py)。

网络边界：使用 SSH 反向转发让远端隔离 Worker 访问本机 18090 API，并将本机 `192.168.8.151:24100/24101` 转发至远端 loopback 服务端口；平台配置 advertised address 与 bind_address 分开。服务监听、探测、客户端请求均是真实进程/网络请求，但不代表公网直连端口或现场防火墙验收。

## 真机发现的代码缺陷及修复

修复前两个服务已 HEALTHY、客户端可完成，但日志目录只有 task scope，原诊断日志出现 `process log unavailable; business execution continues`。服务日志按钮未出现，API 等待 service catalog 超时。

以 `HwHiAiUser` 且清空补充组复现：`runner/process_log_store.py:locked` 用 O_RDONLY 打开只有穿过权限的 `work` 父目录，PermissionError。根因不是 NPU/CANN，也不是业务程序退出失败。前一轮 root 小程序和同账号 GPU 测试没有覆盖这个跨账号权限组合。

Worker/Runner 0.21.8 将祖先遍历改为 `O_PATH | O_DIRECTORY | O_NOFOLLOW`，最终日志根重新取得可读句柄。保留禁止链接、目录身份和实际文件权限检查，不开放其他执行目录、不更改业务结果。协议仍为 17，没有新增数据库迁移。只更新隔离测试组件后重新提交整个双实例批次，完整链路通过；历史缺失的独立正文不能补回。

失败证据：[远端真实异常](traverse-failure.txt)、[修复前回归：3 failed / 4 passed](traversal-before.txt)。新增 8 项非 root 权限测试涵盖 service/business/episode、父目录不可列出、缺少穿过权限、最终根不可读、祖先/根符号链接与非目录拒绝。

## 测试环境准备失败记录

这些属于临时实验配置，不通过放宽生产校验解决：

1. 29 `/usr/bin/python3` 为 3.9，宿主机环境报告不满足 >=3.10；改用节点已有 Python 3.12。业务 ACL 调用使用 ctypes，避免依赖 Python 3.9 的 acl 扩展 ABI。
2. 初版临时目录把 Worker 私有 journal 与业务工作目录放在同一父目录，业务账号不能穿过；调整为独立 private 状态目录。
3. 临时 cache 初始属 root 且不可写，上传代码的隐式父目录受远端 umask 影响为 750；只对临时 cache 授权业务账号、临时代码目录设为可读可穿过。未修改原节点目录或 NFS 权限。
4. 对已 FAILED 的实例再次调用 stop，现有接口返回 409 `terminal job`。这是另一个已记录的终态控制边界，本轮未改服务控制接口；失败任务确认释放资源后取消测试批次。成功运行实例的正常停止和清理已通过。

## 更新与验证边界

已同步执行日志、节点升级文档、界面升级指南、项目 Skill 参考和 OpenSpec 记录。生产节点需要后续正常组件升级才获得修复；仅更新 API 不会替换远端 Runner。本次没有验证 NPU Docker、业务模型推理、任意 NFS 故障、整机日志配额通道或在线滚动升级。上一轮 GPU/Docker 结果见 [真机实验](../process-logs-live-20261119/README.md)。前端、真机业务和容器子进程未插桩，不计入 Python 覆盖率。

清理确认：两个隔离服务停止并释放资源，隔离 Worker、API、SSH 转发均停止，测试数据库容器已停止，远端临时目录已删除。29 原有 8 个 Worker 的 PID 与测试前一致，未停止或改写配置；本机私有测试文件仅留在 `.runtime/`。证据见 [清理结果](cleanup.log) 和 [原 Worker 核对](cleanup-remote.json)。

## 关联回归和覆盖率

最终命令：`PYTEST_ADDOPTS='--import-mode=importlib -k "process_log or log_capture or execution_group_gaps or worker_version"' PROCESS_LOG_TEST_IMAGE=mini-eval-runner:dev .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/npu-log-traversal-final`。234 passed / 3 skipped / 1872 deselected；包括新增 8 项真实非 root 权限测试和实际 Docker 采集矩阵。3 项 PostgreSQL 小连接池/迁移测试未提供其专用测试库变量而跳过；隔离平台实际使用 PostgreSQL 不等于这些破坏性测试已经执行。

修复模块 process_log_store 行覆盖率 98.29%、分支 93.59%；全部 Python 为 35.80% / 15.31%，runner 目录 59.88% / 41.32%，目录与关键功能门槛仍未达到，覆盖率命令因此非零退出。分母、门槛和排除规则未修改，未合并不同运行覆盖数据。首次相关回归为 189 passed / 3 skipped，后续为补齐关联存储用例而扩展范围，不将两轮通过数相加。原始输出见 [回归结果](regression.txt)、[统一口径覆盖率](coverage-summary.md)。

Ruff、TypeScript 检查、Vite 构建、OpenSpec strict 及文档相对链接检查通过。界面仅更新升级指南说明，不改变日志交互；未重新部署生产前端。
