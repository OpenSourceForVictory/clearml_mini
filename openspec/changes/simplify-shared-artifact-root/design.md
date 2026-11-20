# Design
## Context
当前映射必须含节点名单、节点根、API根及marker；领取任务另行判断共享传输。用户已批准实施同路径简化方式。
## Goals / Non-Goals
只设置一个部署变量即可读取已登记的输出；不实现远端磁盘转发、自定义任意路径浏览。
## Decisions
未配置或空高级 JSON 时，从 SHARED_ARTIFACT_HOST_ROOT 生成配置，API固定挂载到 /mnt/mini-eval-artifacts；节点使用变量值对应绝对路径。拒绝 / 与不安全路径。使用规范化节点根的摘要作为稳定配置身份，无需写盘marker。自动映射支持全部已纳管节点，但读取仍要求执行和用户授权。
高级 JSON 非空时完全优先，包括 [] 明确关闭；错误 JSON 拒绝而非退回宽松默认。自动配置使用内部标记跳过磁盘marker，外部JSON不接受该字段。旧快照不转换，不同配置身份拒绝历史读取，保留原JSON即可继续旧记录。
## Risks / Trade-offs
简化模式不能识别同一路径替换为另一块盘；管理员负责正确挂载，需要卷身份校验时继续高级模式。缺目录不会自动创建；路径穿越、链接和跨任务访问保持拒绝。
## Migration Plan
更新 API 和主 compose.yml，保留原部署命令重建API；无需数据库迁移。回滚代码和Compose，历史高级映射不变。普通模式需现有支持shared的Worker，未升级旧Worker边界不变。

## 原部署命令兼容（用户追加要求）
主 deploy/compose.yml 内置两项环境变量和总目录只读绑定；无需显式追加 overlay。Compose 不支持按环境变量省略单条 volumes，未设置变量时绑定仓库内 deploy/empty-artifacts 占位目录，环境变量仍为空，API不启用映射或共享传输。设置变量时绑定真实目录，create_host_path=false 防止错字生成空目录。仅API新增挂载，migrate/bootstrap/onboarding保持原行为；旧overlay与主文件按容器目标路径合并，不产生重复挂载，旧自定义映射保留。与选择自动挂载任意主机父目录或要求用户改命令相比，占位目录范围最小且支持既有Compose版本。
