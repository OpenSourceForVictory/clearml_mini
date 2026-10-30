# Design
## Context
现有 EvaluationSuite 按 owner/name/version 唯一，cases 为 JSON；GET suites 100 项分页但 UI 只读首屏。POST suites 直接保存不可变版本；batch 提交快照已有。见 proposal。
## Goals / Non-Goals
目标：复用现有集合实体与执行快照；用户能在页面完成导入与维护。
非目标：自动发现任意仓库任务、导入时运行客户端代码、团队共享权限、放宽每集合 100 项限制、自动下载资产。
## Decisions
- 集合增加 status(draft/published)、archived、revision、description、tags、source；旧行 published/revision0。草稿也要求有效的 1–100 项和可用客户端；“草稿”指未发布可编辑，非法文件不落库。沿用 owner/name/version 唯一，不另建集合家族表，详情按同名列版本。
- 独立 suites.py 管理接口，batches.py 保留 Case/SuiteCreate 兼容；旧 POST suites 默认 published，新 UI 明确 draft/published。发布/归档/恢复/删除均持同一调度事务锁+expected_revision。发布后内容不可变，归档为独立标志；恢复也检查版本。batch 必须引用未归档 published；idempotency 重放仍返回原批次。
- POST suites/preview 接收 <=1MiB 文本，安全解析 JSON/YAML；拒绝重复键、别名、标签对象、深度/节点过大、非 JSON 标量；PyYAML SafeLoader 并限制解析复杂度。文件格式 schema_version=1，name/version/description/tags/source/client(name/version)/defaults/cases；旧数组兼容。默认参数浅合并，逐项优先。省略 case_id 按位置生成稳定 case-NNN，显式重复报错；不静默删除错误任务。
- 哈希基于规范化 cases，用于同用户重复提示不泄露他人信息，允许用户显式保存新版本。服务端发布前重新验证；导入预览无数据库副作用。导出使用客户端名称/版本作为提示，不导出私有 ID、凭据或运行端点；找不到模板时导入依然可预览，保存需用户选择可用模板。
- RoboTwin 目录作为 backend 包内固定数据，来源 commit 和原清单一致；不在导入时访问公网。用户可搜索勾选并应用回合数、种子、参数。
- 新 suite 管理组件替换旧注册组件，复用 CS 运行页。列表服务端分页筛选+摘要，详情按需加载，草稿/选择不随轮询覆盖。管理保存停留详情，“发布并运行”跳转并保持集合选择，即使集合不在旧首屏。
- 标签最多20个每个40字符、说明2000、source1000、预览1MiB、错误数有界。导入和保存都不检查资产、GPU、服务地址；执行准备仍由原链路处理。
## Risks / Trade-offs
- YAML 解析扩展攻击面 → safe parser、限制别名/深度/节点/字节、类型验证与测试。
- 历史归属 → 旧行发布，不改变 batch/Job 快照；只有未引用草稿允许硬删除。
- 分页选择丢失 → CS 运行页保留已选集合并按 ID 获取。
## Migration Plan
迁移向后加列；先测试旧记录回填与降级保护，备份正式库/代码/镜像后部署 API+前端。Worker 无协议变化无需重启。降级前不能有草稿或归档（旧 API 不认识状态）；需先处理或恢复备份到独立环境，禁止删除历史运行。生产验收用专用测试集合，完成后归档/删除草稿，保留原集合。
