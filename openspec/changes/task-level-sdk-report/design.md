# Design

## Context
Worker 已为每个 execution ID 创建独立 journal/state，SDK 逐回合事件写入该执行的 `work/episodes.jsonl`，Worker 以持久化游标限量读取并经鉴权 API 转发。API 已按 run/execution 保存回合结果并对重放幂等。普通命令目前只按退出码判断，不接收评测指标；`Run.result_json` 是可空 JSON 字段。

## Goals / Non-Goals
**Goals:** 让现有整任务程序可通过简单 SDK 调用报告一个业务结果；不依赖回合编号；隔离并发任务；Worker 统一持有平台凭据并转发；区分业务结果与进程退出状态。

**Non-Goals:** 不自动推断业务成功；不伪造回合；不替用户程序生成业务指标；不改变逐回合契约和服务端调度。

## Decisions
1. 模板接入方式增加 `integration_mode: sdk-task-result`，仅供 `task_type=command` 的整任务程序使用。它与 `sdk-command`（平台按回合包装）语义不同；旧 `direct` 和 `sdk-command` 不变，也不增加数据库字段或迁移。
2. SDK 提供 `report_task_result(success: bool, metrics: dict[str, finite number] = {})`，并允许可选的简短业务摘要。它写入该执行 `MINI_EVAL_WORK_DIR/task-result.json`，先写同目录临时文件再原子替换；只允许一次，重复相同内容幂等、不同内容报错。SDK 不读取或发送平台凭据。
3. Worker 通过 execution 对应的工作目录发现结果，校验有界 JSON 后使用 Worker 身份发送到 `/worker/executions/{execution_id}/task-result`。API 以 `Run.id` 绑定结果，重复同体重放成功、冲突返回 409。Worker 为每个子执行使用自己的状态和路径，多任务并发不会共享游标。
4. 任务级协议结果保存在既有 `Run.result_json` 中的版本化结构 `{kind:"task", success, metrics, summary}`；旧回合指标 JSON 保持原形。模板启用了 `task` 结果但未收到结果时，执行仍按原有生命周期及退出码结束，界面显示“业务结果未上报”；业务 `success=false` 仍可与进程退出码 0 同时成立，执行状态不由业务结果改写。
5. 单任务结果是执行尝试级数据，重试产生新 Run，不覆盖前次结果。历史 Run 结果无迁移、不回填。
6. 页面在批次任务/尝试详情分别展示“程序执行”和“业务结果”；未启用或未上报显示“无业务结果”，不得从退出码推导业务成功。摘要和指标按白名单字段、大小限制展示，避免渲染 HTML。

## Risks / Trade-offs
- 结果文件延迟出现时可能晚于 Worker 完成检查；Worker 在进程存活期间轮询转发，进程结束后再做最终排空；缺失时显示未上报，不将其改写成执行失败。
- 普通业务用户可能无权写 Worker 私有状态，但只写自己执行的工作目录；Worker 负责读取并更新私有游标。
- SDK 文件 journal 适合单个任务最终结果；不将其描述为高频进度流或通用 IPC。
- 现有自定义命令的 Python 环境未必安装 SDK。Docker 基础镜像提供 SDK；宿主机入口通过平台提供的 `PYTHONPATH` 可导入 SDK，接入文档说明边界。

## Validation
覆盖 host/docker、直接命令与 SDK 调用、并发执行隔离、原子文件读取、缺报/重复/冲突/非法指标、API 鉴权/所有权/幂等、Worker 网络失败重试、退出码与业务结果的独立状态，以及旧事件模式回归。真实模型验收不在模拟用例范围内。
