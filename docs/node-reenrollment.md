# 解除与重新纳管

解除纳管撤销旧凭据、移出资源池、保留机器/GPU 身份及历史，不删除磁盘资产。有任务、服务或待清理执行时，先停止新准入，等待清理后再次解除；正在升级的机器需先解决升级。

原来的“注册新机器”不能恢复同名机器。现在从原机器的“查看配置 → 重新纳管”进入：

1. 开始重新纳管，生成新凭据。机器显示“重新纳管中”，保持停用。
2. 填节点可访问的 API 地址，下载凭据 `node-credential.json` 和完整包 `mini-eval-node.zip`。包包含 Worker、runner/SDK、独立升级执行器及安装/SSH 脚本。页面给出 SHA-256。
3. 解压包，在有 SSH 连接能力的电脑运行部署命令。SSH 密钥使用本机 SSH 配置，不上传平台、不写入数据库。
4. 页面分别显示每张原 GPU 和升级执行器的目标版本回连状态。全部验证通过后点击“完成纳管并启用”。
5. 在资源池管理中重新分配 GPU；不会静默恢复旧池归属。

当前提供页面下载/验收与独立 SSH 命令执行器；**网页内直接填写 SSH 信息并后台执行的完整向导尚未完成**。仅生成凭据、下载成功、SSH 返回零或普通心跳均不能代替重新纳管验收。未完成接入不能通过普通“启用”绕过。

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

用户服务使用所属用户执行，填写 `--service user:mini-eval-worker@实例名.service`；每个原服务重复指定 `--service`。同一安装不支持混用系统/用户服务。实例配置从 `HOME/.config/mini-eval/*.json` 发现，必须包含全部原 GPU；不允许遗漏同一安装下的实例。现有代码路径使用 `HOME/.local/share/mini-eval-worker`，自定义旧布局须先确认并通过 `replace_existing(layout, credential)` 受控接入，不盲目覆盖。

## 新机器

“注册新机器”也提供完整包及凭据下载。执行 `deploy_node.py` 时不加 `--existing`、`--home` 和 `--service`；新安装创建用户级 Worker 与独立 updater 服务。默认探测全部真实 GPU，可用 `--gpus 0,1` 指定。需 Python 3.10+、systemd 用户会话，以及配置执行方式所需 Docker/镜像和 NVIDIA 驱动。安装器检查依赖，不自动下载业务镜像、模型或资产。长期运行需要正确配置用户会话/linger。新安装失败留下配置时，不应当作成功；需检查失败组件后用已有安装流程修复。

## 模块和恢复边界

- API 拥有身份、凭据撤销、管理修订和验收状态；重新纳管固定目标发布摘要，全部原槽位连接且独立 updater 的代码摘要匹配才允许完成。
- 独立 SSH 执行器负责传输和调用；Web API 不执行 SSH，也不存 SSH 私钥。每条远程命令有超时。
- 节点安装器停止原 Worker/升级执行器，获取安装及恢复锁；存在未完成升级或执行 journal 时拒绝替换。拷贝完整 Worker/runner，并单独安装升级执行器；保留配置，只有 API 地址和凭据更新。
- 原 NFS/systemd Worker 单元不重写、不删除，不启用第二套 Worker 身份。升级执行器以单独服务运行。
- 替换失败会恢复旧代码和凭据，保持节点停用；旧凭据在平台仍已撤销，因此不能误接任务。修复后重新生成凭据、重试部署。备份目录 `mini-eval-worker.before-enrollment-*` 保留待人工清理。
- 不把进程异常回滚承诺为断电事务：若机器在目录替换瞬间断电或安装进程被强制杀死，应先检查备份与原目录、恢复记录及原服务状态，再重试。不得清空执行 journal 强行安装。
- 完成纳管不等于业务环境就绪；目录回执、资源池分配和模板要求继续由原模块检查。

## 验证

新增后端覆盖身份保留、旧凭据失效、版本修订/权限、双 GPU 验收、错误 updater 摘要、完整包、真实目录替换/失败恢复、journal 拦截和 SSH 私有传输/归档校验。浏览器用例覆盖解除状态进入重新纳管、下载不等于完成、部分 GPU 不可启用、全部验证后完成。

`scripts/smoke_node_enrollment.py` 使用隔离 API（18093）、两个真实 systemd 用户 Worker（模拟 GPU）和从安装包复制的独立 updater 进程。验证原槽位、工作路径及服务挂载依赖保留，全部摘要回连后完成。此测试不等于真实 GPU 仿真评测，也不等于已在五个生产节点重新纳管。独立 updater 的 systemd 安装由安装器测试覆盖；隔离联调使用独立进程，避免修改本机正式 updater 单元。

本轮验证结果：后端全量 301 通过、41 项环境相关跳过；最终版本补充回归 11 项通过，关联前端 10 项通过（资源池用例更新表单文案后重跑通过），TypeScript/Vite 构建、OpenSpec strict、隔离 PostgreSQL 迁移均通过。
