## Context
现有 workers 接口默认包含所有设备，管理员取得明细、普通用户取得计数，缺少角色分类。
## Goals / Non-Goals
提供用途分类，不把机器角色等同于当前可调度状态，不扩大明细权限。
## Decisions
workers 增加可选 client/server role 参数，缺省全量。依据所属 Worker.roles 筛选；无 Worker 的旧槽位视为 client，双角色进入两类但全量仅出现一次。前端切换时清空旧明细，effect 失活保护避免旧分类请求覆盖新分类。资源池仍显示全平台可用池并说明范围。
## Risks / Trade-offs
用途分类不能推断在线、环境就绪或任务准入；页面明确区分这些语义。
