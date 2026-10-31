# Design
## Context
现有模板有 image/command/protocols，Worker 要求 image 在本地 client_images 内；运行入口依赖镜像自带 runner。storage v4 已管理每执行工作/输出目录与固定资产/缓存根，不能表达多个逻辑资产。
## Goals / Non-Goals
目标：兼容旧模板、可跨机器复用自带镜像、显式变量和逻辑路径、可观察就绪。
非目标：自动拉镜像/私有仓库凭据、密钥管理、任意 Docker 参数/特权挂载、非 Python 镜像无依赖执行、自动推断业务回合结果。
## Decisions
- ClientTemplate.runtime 可空 JSON；新用户接口以当前用户为 owner，命名沿用现有全局 name/version 唯一（冲突用通用错误，不泄露所有者）。runtime 包含 interpreter=python/python3、environment、overridable_env、work_dir/output_dir、assets(逻辑名→容器路径)；旧模板 null 保持原运行方式。运行 environment 只覆盖允许项。普通变量明文保存，不当作秘密入口。
- 占位符采用受限美元花括号：task_name/episodes/seed/endpoint/input_path/work_dir/output_dir 和 assets:逻辑名。替换发生在字符串参数/变量内，不 shell 求值。保留 MINI_EVAL_*、NVIDIA_*、CUDA_VISIBLE_DEVICES、PYTHONPATH/PYTHONHOME。平台任务变量和目录变量自动注入。
- 新机器 runtime_revision/runtime_config，配置包含 images 批准列表和 assets 宿主映射；null 恢复本地配置。Slot 保存 runtime_report 与时间。与存储/成员版本独立，管理员写使用调度锁和 expected_revision，释放 RESERVED；v5 监督心跳回报，忙碌时等待，空闲后持久化有效配置和版本。每项资源单独报告可用性/原因，缺少单一资源不阻止其他兼容任务。
- v5 Worker 用 Docker inspect 验证批准镜像存在，安全路径检查和固定受限探针验证逻辑资产可挂载只读，报告定期刷新；镜像不自动拉取。协议门槛和报告新鲜度在调度/claim 中校验；旧任务恢复先处理，不因新配置阻断。提供用户可读、隐藏宿主路径的任务等待原因。
- 新运行模板在 container create 指定解释器 entrypoint，并复制标准库 runner 包到容器临时目录，通过受控 PYTHONPATH 启动 supervisor/cs_entry；无需改用户镜像，要求该镜像包含兼容 Python。默认不改变旧镜像 entrypoint。参数/环境替换在 cs_entry，工作目录可设置。用户程序仍读取任务 JSON 或参数，并按 SDK/协议输出 metrics 与回合事件。
- 固定平台 work/output/assets/cache 挂载继续保留；新模板的 work/output 是同一执行子目录的额外挂载别名，逻辑资产取自节点批准映射。拒绝目标交叠、特殊路径和覆盖平台输入/运行时；宿主路径不由用户选择。Worker 启动前复查，执行日志保存 runtime 快照，重启恢复不借用新映射。
- UI 普通用户在客户端页注册镜像，模板定义 JSON 分组配置；运行页提供 environment JSON 覆盖。机器页新增运行环境面板，可编辑批准镜像/逻辑资产并显示每卡状态。模板保留版本不可变。
## Risks / Trade-offs
- 配置/领取竞争 → 同调度锁、版本确认、并发测试。
- 恶意挂载/变量 → 平台约束、真实 Docker 只读/隔离验收。
- 镜像缺解释器 → 说明必要条件，启动错误可诊断；不伪造成功进度。
- 资产在执行中被宿主管理员删除 → 启动复查和错误上报，映射不承诺资产内容不可变。
## Migration Plan
新增可空模板字段和默认版本/报告字段，旧数据不改。先 API/前端再升级两台 Worker 为 v5。备份库/代码/镜像并记录回退，停止新模板准入并排空后才能回退 Worker；迁移降级拒绝存在新 runtime 模板/节点配置，保留历史快照。
