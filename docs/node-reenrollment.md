# 解除与重新纳管

解除纳管撤销旧凭据、移出资源池、保留机器/GPU 身份及历史，不删除磁盘资产。有任务、服务或待清理执行时，先停止新准入，等待清理后再次解除；正在升级的机器需先解决升级。

从原机器“查看配置 → 重新纳管”进入 SSH 向导（先解除纳管并完成清理）：

1. 使用原安装管理账号填写 SSH 信息，确认主机指纹，检查环境。
2. Docker daemon 需可用，但 runner 镜像缺失不阻止纳管；按页面命令自行准备，纳管不验证容器运行能力。root 系统安装可从独立 updater 的 `/opt/mini-eval-updater/config.json` 识别自定义目录；页面显示原程序路径和全部原 Worker 服务。沿用原业务账号、全部设备、工作目录和资产，不迁移到新 HOME。
3. 确认计划后，重新检查布局、配置摘要和有效 systemd 服务；环境变化则要求重新检查，未完成升级或执行恢复记录会阻止安装。
4. 安装器更新 Agent、Worker、SDK 和独立 updater。全部原设备及两个管理组件以目标摘要回连后，平台才完成纳管。
5. 在资源池管理中重新分配设备；不会静默恢复旧池归属。

布局必须完整、root 所有且不可被组/其他用户写入，路径不能经过符号链接；原服务必须绑定对应配置、程序目录和所选 Python。旧安装缺少可信 updater 布局、混合身份或遗漏设备时，保留拒绝保护。不要删除配置或恢复记录强行重装。当前支持原位恢复，未提供任意目录迁移或业务账号转换。

下列下载包与 SSH 命令作为已有标准 HOME 安装的维护入口，日常操作优先使用界面向导。仅下载成功、SSH 返回零或普通心跳不能代替回连验收。

## 现有 NFS 系统服务

以下以 `/home/huawei`、原系统服务 `mini-eval-worker-nfs.service` 为例，实际用户名和主机由管理员填写：

```sh
unzip mini-eval-node.zip -d node-package
python3 node-package/scripts/deploy_node.py \
  --bundle mini-eval-node.zip --credential node-credential.json \
  --sha256 页面显示的摘要 --target huawei@节点地址 \
  --sudo --existing --home /home/huawei \
  --service system:mini-eval-worker-nfs.service
```

SSH 默认端口 22，支持 `--port`、`--identity`。要求目标已在 known_hosts 中确认，并可用密钥非交互登录；严格校验主机身份，不自动信任新主机。系统服务模式需要 `sudo -n` 权限。没有该权限时，手动通过 SSH 拷贝包和凭据，在节点解压后执行：

```sh
sudo python3 -B node-package/scripts/install_node.py \
  --credential node-credential.json --existing --home /home/huawei \
  --service system:mini-eval-worker-nfs.service
```

凭据文件权限设为 600，使用后删除传输副本。SSH 执行器将包和凭据以标准输入传输到节点 700 临时目录，凭据 600；退出时清理目录。连接中断可能阻止远端清理，应检查 `/tmp/mini-eval-node.*`。密码/令牌不会出现在 SSH 命令参数或进度日志。

用户服务使用所属用户执行，填写 `--service user:mini-eval-worker@实例名.service`；每个原服务重复指定 `--service`。同一安装不支持混用系统/用户服务。实例配置从 `HOME/.config/mini-eval/*.json` 发现，必须包含全部原 GPU；不允许遗漏同一安装下的实例。现有代码路径使用 `HOME/.local/share/mini-eval-worker`，自定义 root 布局由 SSH 向导从可信 updater 发现并绑定快照，安装器通过 `--existing --layout-snapshot` 再次核验后原位恢复；不接受任意布局文件覆盖原安装。

## 新机器

“注册新机器”通过 root SSH 安装系统级 Agent、Worker 和 updater，业务以普通账号运行；具体依赖及 GPU/NPU 区别见 [root 纳管](root-managed-host.md)。标准 HOME 用户安装命令仅用于已有环境维护，不应与新 root 纳管流程混淆。新安装失败不能当作成功，需根据原配置和故障记录恢复。

## 模块和恢复边界

- API 拥有身份、凭据撤销、管理修订和验收状态；重新纳管固定目标发布摘要，全部原槽位连接且独立 updater 的代码摘要匹配才允许完成。
- 独立 SSH 执行器负责传输和调用；Web API 不执行 SSH，也不存 SSH 私钥。每条远程命令有超时。
- 节点安装器停止原 Worker/升级执行器，获取安装及恢复锁；存在未完成升级或执行 journal 时拒绝替换。拷贝完整 Worker/runner，并单独安装升级执行器；保留配置，只有 API 地址和凭据更新。
- 原 NFS/systemd Worker 单元不重写、不删除，不启用第二套 Worker 身份。升级执行器以单独服务运行。
- 自定义布局替换失败会恢复旧代码、Worker 凭据，以及 Agent/updater 配置、程序文件与服务单元的内容、属主和权限，保持服务停止及节点停用；旧凭据在平台仍已撤销，因此不能误接任务。修复后重新生成凭据、重试部署。备份目录 `mini-eval-worker.before-enrollment-*` 保留待人工清理。
- 不把进程异常回滚承诺为断电事务：若机器在目录替换瞬间断电或安装进程被强制杀死，应先检查备份与原目录、恢复记录及原服务状态，再重试。不得清空执行 journal 强行安装。
- 完成纳管不等于业务环境就绪；目录回执、资源池分配和模板要求继续由原模块检查。

## 验证

新增后端覆盖身份保留、旧凭据失效、版本修订/权限、双 GPU 验收、错误 updater 摘要、完整包、真实目录替换/失败恢复、journal 拦截和 SSH 私有传输/归档校验。浏览器用例覆盖解除状态进入重新纳管、下载不等于完成、部分 GPU 不可启用、全部验证后完成。

`scripts/smoke_node_enrollment.py` 使用隔离 API（18093）、两个真实 systemd 用户 Worker（模拟 GPU）和从安装包复制的独立 updater 进程。验证原槽位、工作路径及服务挂载依赖保留，全部摘要回连后完成。此测试不等于真实 GPU 仿真评测，也不等于已在五个生产节点重新纳管。独立 updater 的 systemd 安装由安装器测试覆盖；隔离联调使用独立进程，避免修改本机正式 updater 单元。

本轮验证结果：后端全量 301 通过、41 项环境相关跳过；最终版本补充回归 11 项通过，关联前端 10 项通过（资源池用例更新表单文案后重跑通过），TypeScript/Vite 构建、OpenSpec strict、隔离 PostgreSQL 迁移均通过。

## 本机发布记录（2026-11-13）
代码 `8ef72d3` 已发布至本机 18000 控制端；保留旧镜像与数据库备份，数据库由 l285a67 迁移至 n407c89。发布前无活动执行，14 条历史执行保持成功；发布后真实登录、完整安装包 SHA-256、管理界面和版本面板检查通过，五台机器/五张 GPU 在线。未解除或更新生产 Worker；原 NFS 单元保持原状。发布证据位于 `.runtime/releases/reenrollment-8ef72d3/`。当前五节点安装新 Worker/升级执行器仍需按上述部署流程执行，不应把控制端上线视为节点已升级。
