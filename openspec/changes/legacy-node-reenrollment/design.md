## Context
旧节点 /opt/mini-eval-updater/config.json 已记录 app_dir、state_dir、全部 config_paths、services、agent。沿用原布局比移动到新 HOME 更能保留业务身份及依赖。
## Goals / Non-Goals
支持 root 系统旧安装的原位重新纳管；不接管缺失/不可信布局，不转换业务账号，不重写 Worker units。
## Decisions
使用共享纯 Python 布局验证模块，随 SSH 探针源码及安装包分发。仅读取固定 root updater 配置，检查非符号链接、root 所有权和写权限；核对有效 systemd 服务、配置全集、身份一致、解释器及恢复记录。向导保存脱敏布局与摘要，安装前重检，安装器再次比较。控制面仍要求解除纳管且无活动执行；全部原槽位、Agent、updater 摘要回连后完成。
保留原位代码备份及配置。辅助 Agent/updater 配置、代码和单元在失败时恢复；保持服务停止及平台停用，不把旧凭据恢复等同于重新授权。
## Risks / Trade-offs
不支持缺失 updater 布局或混合身份；明确拒绝。断电/强制杀进程仍需依据原备份恢复，不承诺跨目录事务。真实节点测试不得清空恢复记录。
## Validation
覆盖八卡成功、路径/服务歧义、配置变更、未完成执行/升级、失败回滚及包完整性。保留全量覆盖率失败/跳过与真机验证边界。

## 用户确认的镜像边界
纳管只验证 Docker daemon 可用，runner 镜像 inspect 作为提示。缺失不阻断安装，无 pull/build/run；默认 runner 提供项目/安装包目录下的构建命令，自定义镜像提供实际引用的拉取命令。旧“自动准备”需求由本决定替代。业务镜像的批准、存在性与调度门槛继续由 image_runtime 管理；纳管完成不代表容器运行验收。
