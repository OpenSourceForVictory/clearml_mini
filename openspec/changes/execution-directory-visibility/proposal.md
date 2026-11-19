## Why

用户需要从任务详情定位节点上的 runs 目录。目前任务详情显示任务 ID 和尝试序号，不能据此得到执行目录；执行数据接口只返回有清理状态的条目，也不能当作目录是否上报的证据。

## What Changes

- 任务详情和批次任务排查按所选尝试展示 execution_id，与 job_id/run_id 分开。
- 展示该尝试已上报的节点执行目录及扫描时间、数据状态，提供独立复制按钮；尚未分配、尚未上报和多个历史路径明确区分。
- 从与所选 Run 关联的 ExecutionData 读取目录，不用节点当前工作配置猜测历史路径；不以清理状态非空为展示条件。
- 保持权限和旧客户端字段兼容，复制失败提供提示及可选择文本。
- 同步用户指南和操作文档，补充定向 API 与界面测试。

## Capabilities

### New Capabilities
- `execution-locations`: 按执行尝试查询、展示及复制节点执行身份和已上报数据目录。

### Modified Capabilities
无。

## Impact

backend/app/main.py 的任务详情、独立目录查询/展示辅助模块、ExecutionData 查询；frontend/src/main.tsx、batch-detail.tsx 与共享展示组件；docs/batch-monitoring.md、host-execution.md、界面用户指南和项目 Skill 参考索引。预计不修改数据库结构或 Worker；现有 Worker 目录扫描提供数据，未扫描的历史目录显示未上报。此次不改每回合日志采集，不增加远程文件浏览或清理操作，不把容器内路径与宿主机路径混为一谈。
