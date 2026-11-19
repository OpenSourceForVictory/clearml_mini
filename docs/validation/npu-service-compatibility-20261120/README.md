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

## 探测会话修复后第三轮

Worker 会话申请移入启动续租保护，在启动期限内重试 503/网络故障。29 节点仅应用这处代码补丁（备份原文件），重启 NPU 2/3 Worker；其余 Worker 未重启，不宣称整节点已完整升级。最终 API 镜像 `mini-eval-api:probe-session-retry-20261120` 含对应 Worker 源码和更新指南。

模板版本 3 两个实例均完成真实张量计算并 HEALTHY，运行用户 HwHiAiUser，DeviceAllow 分别仅包含加速卡 /dev/davinci2、/dev/davinci3（另有必要公共管理设备）。日志包含各自 PID、503 预热、200 就绪，UI 历史日志隔离验证通过。之后 STOPPED、cleanup_confirmed=true、held=0；systemd 单元 inactive，19500/19501 无监听。见 final-result.json。测试记录保留在管理页。

真机成功没有注入 API 503；临时故障重试由 host/Docker 自动测试验证。专项 67 passed（初次测试 3 个失败为新增时限检查与旧断言/非临时状态码预期不一致，调整断言后通过）；统一覆盖率另记录，不能将这些次数相加当覆盖率。

最终统一定向统计：96 passed、22 skipped、2190 deselected；services.py 行 98.48% / 分支 72.22%，worker/client.py 行 54.52% / 分支 39.94%，关键功能门槛仍未达标。跳过原因：requires Docker and delegated systemd。原始统一报告 `.runtime/probe-retry-coverage-20261120/`。发布后首次浏览器请求遇到容器尚未监听的 ERR_SOCKET_NOT_CONNECTED，待 API healthy 后重跑通过；不隐去首次失败。
