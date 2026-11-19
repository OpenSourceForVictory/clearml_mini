# NPU 服务与日志验证（2026-11-20）

API 镜像：`mini-eval-api:service-log-compatibility-20261120`。无数据库迁移，未升级节点 Worker。

## 已验证

29 节点第一轮同名服务分别指定 NPU 2/3，均完成真实 torch_npu 张量计算（平方和 1240），HTTP 503 预热后转为 200，平台由 STARTING 转为 HEALTHY。它是张量与服务生命周期验证，不是业务模型评测，也不是批次客户端分配验收。NPU 不产生产物，无共享存储验证要求。

实例 `4b31439a-e088-4aa1-a557-82952be9e90d`、`854b75e0-649b-4e0f-93cf-748536af5fda`。业务 PID 分别 1478608 / 1478931，UID 1001。逻辑可见设备均为 0；不能把它误解为物理卡相同，物理隔离仍需运行时 DeviceAllow 核对。

首次验证发现服务日志接口为空，而任务日志有 v2 分片；新增两个回归测试在修复前均失败（content 为空）。API 改为优先读取 v2 状态对应的保留窗口，兼容旧表，空 v2 不回退旧日志。生产浏览器验证两个实例的历史日志可见、PID 不串；记录位于管理设置 → 机器管理 → 服务端机器 → 推理服务实例，模板“ NPU双实例宿主机验收-20261120 / 1”。

## 保留的失败

第二轮实例 `63e4d22c-fba0-453e-b267-b7e950ad64d0`，执行 `faeac100-71e5-4596-bcec-aa9ffb0c698e`，启动后申请 service-probe-session 收到 `503 control plane busy; retry shortly`。Worker 随后停止单元并保留 created journal，恢复报 `previously created host unit is missing; source=journal-created`。systemd 记录 00:25:20 启动并停止。不是已证实的同名冲突；这轮双实例复验失败，不能以第一轮成功覆盖。

两轮测试均停止并确认清理，节点 held_executions=0，测试模板禁用，记录保留。

## 自动验证与边界

统一工具 `python -m scripts.test_coverage`，用 PYTEST_ADDOPTS 选择 service_log_compatibility / test_services / execution_logs_v2：26 passed，2270 deselected。services.py 行覆盖率 86.29%、分支 56.94%，未达到关键功能 90%/80%；定向运行的全局门槛也未达到，不宣称覆盖率合格。证据保存在本地 `.runtime/service-log-compatibility-coverage-20261120/`，不提交私有原始数据。前端构建通过；浏览器实测未计入 Python 覆盖率。
