# 批次报告与结果比较

在「CS 评测集合运行」的批次详情下载 JSON 或 CSV 报告。列表最多选择两个批次，可跨页选择，然后点击「比较所选批次」。返回列表保留当前页码、选择和运行表单草稿；比较页地址可收藏或刷新。比较采用手动刷新，失败保留旧快照并提示错误。

## 报告口径

报告只统计每项任务的最新尝试。重试前的历史回合仍可在尝试详情查看，但不重复累计进报告。`schema_version=1`，`generated_at` 为服务器 UTC 时间，`batch` 保存批次标识和状态，`summary` 为批次聚合，`cases` 每项一行。

- `episodes` / `planned_episodes`：计划回合数；`completed_episodes`：已完成回合，不包含执行异常。
- `successes`：目标成功；`goal_failures`：已完成但目标失败；`episode_errors`：回合执行异常。
- `success_rate`：目标成功数 / 已完成回合数；无完成回合时为 JSON null、CSV 空值。批次按完成回合加权，不平均各任务成功率。
- `episode_reporting=false` 的客户端不推断回合结果；这些项以未知结果呈现。
- `partial=true`：至少一项未完整成功执行、未声明上报、完成回合数与计划不符或有回合异常。取消、排队中等状态保留已上报的部分结果，不把未执行回合计作目标失败。
- `job_id/run_id/attempt`：所统计的执行尝试；`parameters_digest/definition_digest`：规范化 JSON 的 SHA-256 摘要。
- `config`：保存的客户端名称/版本/镜像、资源需求、推理地址及环境摘要。参数、环境原值、命令、宿主路径、自由文本错误均不导出。报告不打包二进制产物。
- 普通命令可选择 `sdk-task-result` 并报告一次整任务业务结果。`outcomes.task_result` 统计 success/failure/unreported/not_applicable，单项还包含 `task_result_state` 与 `task_result_data`。不按回合展开，也不改变程序退出状态。

每次导出使用独立只读一致事务。两个批次也在同一事务读取；并发重试不会导致状态、尝试和回合统计来自不同时刻。导出不锁定调度器、不创建任务、不保存报告文件到服务端。

## 比较口径

按 `case_id` 联集对齐。任务名称、种子、回合数、参数摘要均相同，而且双方执行成功、声明上报、完成全部计划回合且无回合异常时，才显示差值。目标失败属于有效完成回合，不阻止比较。

差值为 **右侧成功率 − 左侧成功率**，页面单位为百分点。只在每项都一一对应且可比时显示整体差值，不用共同子集冒充整批比较。缺项、定义差异、未上报或执行不完整会显示原因。集合 ID 相同不保证条件相同，集合 ID 不同也可以比较。

客户端版本、镜像、环境摘要、资源要求、推理地址差异单独展示。外部模型版本未验证：相同地址可能使用不同 checkpoint；请用批次名称标识实验。平台不自动作模型优劣或统计显著性结论。

## API

仅批次所有者或管理员可访问；比较中任一侧不可见，整体返回 404。

```bash
# TOKEN 为已登录用户的访问令牌；不要把令牌放进 URL。
curl -f -H "Authorization: Bearer $TOKEN" "$API/api/v1/batches/$BATCH_ID/report?format=json" -o report.json
curl -f -H "Authorization: Bearer $TOKEN" "$API/api/v1/batches/$BATCH_ID/report?format=csv" -o report.csv
curl -f -H "Authorization: Bearer $TOKEN" "$API/api/v1/batch-comparisons?left_id=$LEFT&right_id=$RIGHT"
```

CSV 使用 UTF-8 BOM，可由常用表格工具识别中文；标准 CSV 引号保留逗号和换行。可能被解释为公式的文本添加单引号前缀，数值字段保持数值，未知值为空。附件文件名仅使用平台生成的批次 ID。


## 独立结果统计

JSON 报告包含 `outcomes.execution`（normal/abnormal/running/unknown/not_started）、`outcomes.protocol`（planned/success/failure/invalid/errors/unreported/not_applicable_tasks/valid/success_rate）和 `outcomes.task_result`（success/failure/unreported/not_applicable）；每项及批次使用相同分类。每项另有 process_scope、wrapper_exit_code，任务级 SDK 单项结果含状态、数值指标和可选摘要。CSV 追加 program_*、protocol_*、task_result_*、process_scope、wrapper_exit_code 列，不删除原有字段，schema_version 保持 1。

新界面和新消费方使用 outcomes；原 summary/progress 为兼容旧消费方保留。普通命令在协议统计中不适用，比较返回 protocol_not_applicable，不再对 null 成功率相减；没有有效结果返回 missing_valid_results。结果异常不自动视为业务失败，异常和未上报计数必须与有效结果成功率同时解释。批次比较仍要求完整、可比的协议结果。

## 时间导出

JSON 保留 schema_version=1，新增顶层 timing（批次 elapsed）及 cases[].timing（当前尝试 execution/waiting），统一 generated_at/as_of；与监控页使用相同投影。CSV 追加 batch_elapsed_seconds、execution_seconds、waiting_seconds、execution_time_state。原始单位秒，未知为空，不将历史缺失当作 0。运行中导出是该次快照；与稍后打开的页面可以存在时间差。详见[执行时间口径](batch-monitoring.md#执行时间execution-duration-display)。
