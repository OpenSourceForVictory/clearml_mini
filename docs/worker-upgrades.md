# 节点组件版本与升级管理

宿主机登录环境来源与客户端逐回合启动需要 Worker 0.18.0 / 协议 15。控制端在模板准入和调度时检查协议；旧节点继续运行旧模板，不能领取使用新字段的模板。发布包随控制端包含新的 `runner/host_episode.py`，节点升级后按每卡回连的软件版本、协议与摘要确认生效。操作与现场验证边界见 [宿主机执行](host-execution.md)及[验证记录](validation/host-shell-episode-launch/README.md)。

入口：管理设置 → 机器管理 → 查看配置 → 版本升级 → **节点组件版本与升级（Worker / Runner / Agent）**。

软件版本、协议版本和发布摘要分别展示到每个 GPU 实例。`worker/version.py` 是开发维护入口：`SOFTWARE_VERSION` 标识软件发布，`PROTOCOL_VERSION` 表示通信能力，`CAPABILITIES` 描述功能。协议版本并非可编辑的节点配置；升级应更换实际程序，不能手工修改数据库版本号。旧 Worker 未报告软件版本时显示“未知”，不根据协议号推测。

## 操作流程

1. 管理员点击“发布控制端随附节点组件包”。平台从控制端已部署的 worker/runner/node_agent Python 源码生成不可变发布包，用 SHA-256 标识内容；相同包重复发布返回同一记录。软件版本由开发发布定义，管理员选择发布包。
2. 确认目标节点所有 GPU 在线、独立升级执行器在线，选择目标版本并点击“排空并升级此机器”。
3. 节点停止接新任务，未领取预留回到队列；正在执行的任务继续续租、上报和清理。持续运行的推理服务会阻止排空，需要管理员先停止它们。
4. 全机排空后执行器取得安装许可，停止配置的原 Worker 单元、检查本地锁与所有恢复 journal、校验并展开发布包，然后替换代码并重启原单元。
5. 平台等待该节点**所有原 GPU 实例**回报目标软件版本、协议、能力和发布摘要，才确认成功并恢复接单。原来的节点停用状态不会被升级自动改为启用。

支持选择历史发布包作为目标进行受控回退；它仍需要当前 API 能兼容目标协议。发布包包含 Worker、Runner 与节点 Agent Python 程序；Agent 仅在升级执行器已配置托管时一并重启更新，不升级 Python、CUDA、驱动、业务镜像或依赖。发布列表来自当前平台保存的包，不联网下载第三方程序。

## 一次性安装独立执行器

旧节点不能直接自行升级，需要管理员先安装升级执行器。执行器使用该节点原 Worker 凭据，仅访问自身升级任务；凭据继续保存在原配置文件，不上传到界面或发布包。

先准备一份配置，路径均为该节点的实际绝对路径：

```json
{
  "app_dir": "/home/huawei/.local/share/mini-eval-worker",
  "state_dir": "/home/huawei/.local/state/mini-eval-updater",
  "config_paths": ["/home/huawei/.config/mini-eval/fleet-gpu0.json"],
  "services": [
    {"scope": "system", "name": "mini-eval-worker-nfs.service"}
  ],
  "verify_timeout": 120
}
```

`config_paths` 必须列出此安装目录共享代码的全部 GPU 配置，不只是第一张卡；`services` 填原有全部 Worker 服务单元，用户单元 scope=user，系统单元 scope=system。使用 system 服务时执行器需有管理该服务的权限，推荐以系统单元运行。示例中的账号和路径必须按节点修改。

在含本项目源码的目录执行（以系统单元为例）：

```bash
sudo python3 scripts/install_worker_updater.py --config /absolute/path/updater.json --system --start
```

纯用户服务使用原 Worker 账号运行，省略 `sudo` 和 `--system`。安装器仅创建独立的 `mini-eval-updater.service`，不修改原 `mini-eval-worker-nfs.service`、Worker JSON、NFS 配置或工作目录。它把执行器复制到代码升级目录之外，避免升级自己时中断。替换已有执行器前先停止它，使用 `--update`；安装器不接管未列出的服务。

本地恢复状态和执行器状态应在本地盘，不要放到共享资产目录。执行器会检查未列出的 Worker 配置、状态锁和残留 journal；任何一项不符合即拒绝替换代码。安装路径不能是符号链接，凭据与 journal 必须在代码目录外。

## 状态和恢复

- **等待全机任务排空**：可取消，恢复正常接单；不主动杀业务任务。
- **安装中 / 等待全部 GPU 回连**：不可取消，避免安装与调度竞争。
- **恢复旧版本中**：调度仍暂停，先恢复旧代码再重启原单元。
- **升级失败**：若旧代码已恢复，可检查后点击“确认失败已处理，解除升级暂停”，再重新发起升级。该操作不会把停用机器强制启用。
- **恢复旧版本失败**：保持暂停且不能强行取消，修复节点服务或本地 journal 后执行器会重试恢复。查看 `journalctl -u mini-eval-updater.service`；用户单元加 `--user`。

发布响应丢失按相同升级身份重试；若平台已确认升级成功，执行器不得因为丢失响应而回滚已恢复调度的节点。安装目录交换前记录本地事务，进程中断后可从旧目录备份恢复。旧包保留为安装目录旁 `.mini-eval-backup-<升级ID>`，失败的新包可能保留为 `.mini-eval-failed-<升级ID>`；确认不再需要后管理员可清理，当前不自动删除备份。

程序校验包摘要、成员路径、文件类型、总大小和 Python 语法；不接受任意下载 URL 或安装脚本。摘要校验保证包内容与平台发布一致；控制端及其管理员仍是发布信任来源。

## API 与验证

- 管理员：GET/POST `/api/v1/admin/worker-releases`；GET/POST `/api/v1/admin/nodes/{id}/upgrades`；POST `/{upgrade_id}/cancel`（带 expected_revision）。
- 执行器：POST `/api/v1/worker/upgrades/poll`，GET `/{id}/archive`、`/{id}/state`，POST `/{id}/report`；均使用节点原凭据，拒绝访问其他节点升级。
- 数据库迁移 `n407c89`：兼容新增软件元信息、发布包和升级历史，不覆盖现有身份与业务记录。

自动化测试覆盖权限、旧节点、修订冲突、多卡回连、排空与预留释放、归档校验、原配置保留、安装失败、中断恢复、成功确认丢失、恢复失败继续暂停。

`scripts/smoke_worker_upgrades.py` 只使用隔离 `127.0.0.1:18093` API，创建两个临时 systemd 用户 Worker，以模拟 GPU 验证真实代码交换和服务重启。历史真机验收曾验证 0.9.0 到 0.10.0，以及两个 GPU 身份、凭据、恢复路径和挂载依赖保留；临时服务完成后删除。当前脚本从本地 `worker.version.SOFTWARE_VERSION` 读取目标版本，在临时副本设置一个不同的测试版本，检查升级后的版本和发布摘要；不再写死历史版本号。首次运行自动创建测试父目录，无法识别版本声明时在创建节点和启动单元前失败。`test_smoke_upgrade_script.py` 对这些行为及升级失败、身份变化、配置/单元被改动进行故障注入测试；这不等于重新完成当前版本的真实 systemd 升级验收。它不是五台生产节点的滚动升级验收。

本功能已部署，已在本机 GPU 和 29 NPU 节点验证托管升级。旧节点仍需先安装独立升级执行器，再由管理员逐台发起升级。

Runner 没有独立版本号，随节点组件包更新宿主机代码；使用平台运行适配器的新容器会复制新版 Runner。旧模式使用镜像内置 Runner，必须另行重建镜像。组件包使用 Worker 软件版本作为标签，Agent 有独立版本号；不能要求三个组件版本字符串相同，应核对发布摘要与回连状态。当前按钮为“目标节点组件发布版本”和“排空并升级此机器”。


退出码上报：Worker 0.18.2 保持协议 15，完成请求增加可选 exit_code，Runner 逐回合终态事件也可含实际业务子进程退出码。先部署迁移 x407a89 与 API，再升级节点，避免旧 API 拒绝新增事件字段；旧记录不回填。详情见 [批次监控](batch-monitoring.md)。容器内旧 Runner 需要按本页的注入/镜像更新方式更新。

日志 v2 升级顺序：备份数据库，迁移至 y518b90，发布 API/前端，再升级所选节点 Worker/随包 Runner 至 0.21.0。协议版本仍为 17，以 bounded_logs_v2 能力协商；旧 API 未下发 log_protocol 时新 Worker 保持旧行为，已有 journal 不切换。先完成在途任务再回退节点，保留新增日志表；降级迁移会删除 v2 历史。见 [执行日志说明](execution-logs.md)。

Worker 0.21.2 修复宿主机显式 per_episode 的回合数、种子、输入及目录映射时机；必须更新 Worker 和随包 Runner 后提交新任务。无需额外数据库迁移，Docker/整任务契约不变。见 [宿主机参数映射说明](host-execution.md#单回合参数映射修复worker-0212)。

Worker 0.21.4移除宿主机/Docker业务固定256进程线程限制，支持节点可选 `execution_pids_limit`。需要升级全部相关每卡Worker，新执行生效；无数据库迁移、协议仍为17、不需要重建业务镜像。配置和运行时默认值边界见[宿主机执行](host-execution.md)。

0.21.3的null/省略表示继承运行时默认；0.21.4改为显式TasksMax=infinity、Docker --pids-limit=-1。已有正整数继续生效；CPU/内存和短时探测不变。


Worker/Runner 0.21.5 增加回合结果受控诊断：程序退出 0 与协议结果异常分别展示，平台 API/前端、报告同步两套结果统计。需升级实际执行节点 Worker/Runner；若业务镜像自带 Runner，需按现有镜像流程重建并更新引用。SDK 下载包同步更新。无数据库迁移，协议仍为 17；旧错误文本和缺失的业务退出码不会被自动还原。详见 [宿主机执行](host-execution.md)。

## 0.21.6 独立日志接线

协议仍为 17，新增 `process_logs_v1` 能力。先更新 API/前端并确认数据库已有 `z629c01`，再通过原节点升级流程更新选定 Worker；Runner 的 process_log_capture、process_log_store、process_log_run、process_log_contract 模块须与包一同更新，不仅替换 client.py。新任务才协商该能力，旧执行保持原行为。容器使用独立执行日志挂载和支持注入的 Runner，旧镜像不强制启用。此文档不是节点已升级的证明。详见 [执行日志](execution-logs.md)。

## SDK 和使用指南是否随更新生效

节点组件发布包包含整个 `runner/`，包括原生 Python SDK `runner/sdk.py`。只拉取或提交仓库代码不会自动升级节点；先部署控制端源码、发布组件包，再对指定节点排空升级并核对发布摘要。

- 宿主机：平台入口使用节点安装目录中的 Runner；业务进程的 PYTHONPATH 默认包含该目录。业务自行更改导入路径或携带同名 runner 包时，要核对实际导入来源，不能仅凭 Worker 版本判定。
- 容器运行适配器：Worker 在新容器执行中复制当前节点的 Runner。升级节点后新执行采用新版；已有执行不热替换。
- 旧式镜像内置 Runner 或业务自行安装的 SDK：节点组件升级不修改镜像或业务环境，需单独更新并验证。
- 界面使用指南随前端版本发布；仓库操作文档和 Skill 随代码变更同步。提交文档并不等于现场页面已更新，需要部署对应前端。

原生 Python SDK 程序在客户端模板选择“直接评测协议”（`integration_mode=direct`），由业务程序导入 EvaluationClient；不是新增名为 sdk 的接入方式。

## 0.21.7 日志轮转恢复修复

更新随包 Worker/Runner，协议仍为 17。此版修正轮转索引提交失败导致历史分片丢失的问题，并在历史目录同步时恢复未完成的清理意图。无需新增数据库迁移，旧任务的有效 manifest 可继续读取；新任务及历史清理恢复须使用更新后的节点组件。依旧通过排空升级，不热替换正在运行的业务进程。本次验证未部署或升级现场节点。参见 [执行日志](execution-logs.md)。

## 0.21.8 普通业务账号日志修复

0.21.8 修复宿主机业务账号只有父目录穿过权限时，独立日志采集器错误要求列目录权限的问题。升级节点组件后新任务/服务使用修复后的 Runner；无需修改工作目录权限或新增数据库迁移，不恢复旧执行未采集的正文。自带旧 Runner 且不使用平台适配器注入的镜像仍需单独更新。协议版本保持 17。真机验证只升级隔离测试 Worker，不等于生产已纳管组件已经更新。
