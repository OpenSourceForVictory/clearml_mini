# Worker 版本与升级管理

入口：管理设置 → 机器管理 → 查看配置 → **Worker 版本与升级**。

软件版本、协议版本和发布摘要分别展示到每个 GPU 实例。`worker/version.py` 是开发维护入口：`SOFTWARE_VERSION` 标识软件发布，`PROTOCOL_VERSION` 表示通信能力，`CAPABILITIES` 描述功能。协议版本并非可编辑的节点配置；升级应更换实际程序，不能手工修改数据库版本号。旧 Worker 未报告软件版本时显示“未知”，不根据协议号推测。

## 操作流程

1. 管理员点击“发布控制端随附 Worker 包”。平台从控制端已部署的 worker/runner Python 源码生成不可变发布包，用 SHA-256 标识内容；相同包重复发布返回同一记录。软件版本由开发发布定义，管理员选择发布包。
2. 确认目标节点所有 GPU 在线、独立升级执行器在线，选择目标版本并点击“排空并升级此机器”。
3. 节点停止接新任务，未领取预留回到队列；正在执行的任务继续续租、上报和清理。持续运行的推理服务会阻止排空，需要管理员先停止它们。
4. 全机排空后执行器取得安装许可，停止配置的原 Worker 单元、检查本地锁与所有恢复 journal、校验并展开发布包，然后替换代码并重启原单元。
5. 平台等待该节点**所有原 GPU 实例**回报目标软件版本、协议、能力和发布摘要，才确认成功并恢复接单。原来的节点停用状态不会被升级自动改为启用。

支持选择历史发布包作为目标进行受控回退；它仍需要当前 API 能兼容目标协议。本期发布包仅为 Worker/runner Python 程序，不升级 Python、CUDA、驱动、业务镜像或依赖。发布列表来自当前平台保存的包，不联网下载第三方程序。

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

`scripts/smoke_worker_upgrades.py` 只使用隔离 `127.0.0.1:18093` API，创建两个临时 systemd 用户 Worker，以模拟 GPU 验证真实代码交换和服务重启。已验证从 0.9.0 到 0.10.0、两个 GPU 身份及凭据/恢复路径/挂载依赖保留；临时服务完成后删除。它不是五台生产节点的滚动升级验收。

本功能尚未部署生产；上线顺序是备份及迁移 API、部署界面、为节点安装独立执行器，再由管理员逐台发起升级。
