# 恢复与兼容专项验收（2026-11-26）

本轮先完成重连、执行矩阵及兼容/迁移/发布验收；按用户要求，不运行长时间负载或全项目覆盖率。以下通过数不能替代覆盖率门槛或百人、百节点容量结论。

## 已完成验证

| 范围 | 入口与结果 | 边界 |
|---|---|---|
| PostgreSQL 恢复控制、收据、结果、完成、领取及调度；Worker 补报 | test_recovery_control/records/results/finish/claim_integration/claims/scheduling/service_deletion、test_worker_recovery_poll/supervisor/finish：349通过、2条件跳过 | 一次性 PostgreSQL16.8；2项真实运行跳过由下一项补跑 |
| 双端能力协商、pending请求、真实完成清理、SDK结果 | test_worker_recovery_poll/finish、test_task_result_reporting、test_worker_execution_recovery：112通过、无跳过 | 开启 RECOVERY_RUNTIME_TEST=1，含原先跳过的宿主机和Docker清理回复丢失 |
| API 注册/心跳确认及旧Worker兼容 | test_recovery_claim_integration、test_execution：22通过 | API关闭开关、无能力Worker不误启用；旧API缺确认时新Worker沿旧领取 |
| 真实执行主循环及实例身份 | test_recovery_execution_real、test_recovery_runtime：45通过、无跳过 | 真实systemd/Docker短程序，隔离API；覆盖取消、超时、重启、有限断联、迟到终态、stdout爆量、批量通道拒绝 |
| 接入与启动组合 | test_recovery_protocol_matrix：12组合通过，任务级SDK另2通过 | host整任务/逐回合、Docker整任务 × 普通命令/直接协议/Python SDK/SDK命令；另host/Docker任务级SDK。Docker无逐回合选项，不虚构支持 |
| 真实日志磁盘耗尽 | test_log_capture：11通过、无跳过 | 宿主机私有mount namespace与Docker小tmpfs，不填满宿主机实际磁盘；业务继续、失败协议采集不冒充结果成功 |
| 真实多卡 | accelerator_multicard_smoke.py，29节点NPU0/4 | 两个同名服务、两个客户端均算出32.0；四个不同InvocationID，停止第二服务后其余三个仍运行；全部测试unit已清理 |

多卡验收使用隔离transient unit与RecoveryRuntime，未通过平台资源调度，也不是RoboTwin或模型质量评测。平台无活跃任务时才安排部署。NPU其他六张在运卡未参与测试。

## 已发现并修正

容器内root运行任务级SDK时，原task-result.json为0600，非root Worker读取触发PermissionError；修复为新结果文件0644，仍仅属主写，锁文件0600，执行私有父目录不放宽。真实host/Docker任务级SDK复测2通过。旧镜像中的SDK需更新；旧文件权限不自动修改。

新增Worker0.21.20显式节点开关与API心跳确认：仅双端明确启用才创建新恢复领取。关闭开关后原pending领取与已分配执行继续原协议，不能静默降级或重新启动。未启用节点维持原行为。

## 失败证据

- 初次接入矩阵使用无效夹具枚举original，改为whole_task后12组合通过。
- 任务级SDK首次host通过、Docker权限失败，原始报告recovery-task-report-20261126.xml；修正后recovery-task-report-fixed-20261126.xml为2通过。
- 初次直接调用pytest入口缺仓库PYTHONPATH，4项导入错误；改为同解释器python -m pytest后112通过。属于测试启动方式错误。
- 本轮未重新统计全项目覆盖率，不声称达标。详细JUnit在本地.runtime中保留，未提交私有运行目录。

## 迁移与回滚边界

上述PostgreSQL专项实际运行ae185f6、af296a7、b03a7b8、c14d09e迁移的升级/降级；存在授权、控制决定、领取快照或结果收据时拒绝破坏性降级。新协商开关和结果文件权限不新增迁移。已有恢复执行不能回滚到不理解其journal或收据的旧Worker；关闭新分配并保留当前版本补报能力，不删除队列、journal和数据库记录。

## 本机发布及完整协议灰度

部署镜像 `mini-eval-api:recovery-acceptance-20261126`，迁移头仍为c14d09e，无新增迁移。发布前活跃执行/未清理数均0，已备份；四角色健康通过，认证/批次列表/节点管理均200，原13个设备槽位和6个Agent重新上报。只对隔离测试Worker显式启用能力，经真实网关、五个API进程、PostgreSQL、Worker主循环和Docker执行两条mock短程序：

- 断联中完成：任务290c80e4-0543-454e-ada2-c5d14c43e794恢复后SUCCEEDED，单次执行，清理确认。
- 断联中用户取消：任务74ab0545-fa1d-4dac-9de2-53bce1dbd820恢复后CANCELLED，单次执行，清理确认。

采用仅测试Worker使用的本地HTTP故障代理，不断开其他节点。测试结束关闭新分配开关、停止隔离Worker，保留数据库恢复收据；未把现有节点批量升级或切换协议。宿主机完整主循环/取消/看门狗/资源收尾由上述45项真实systemd/Docker隔离用例验证，生产灰度两条为Docker mock程序，不能声称真实模型验收。首次灰度夹具错误地PATCH资源池gpu_model被400拒绝；改为创建时使用实际模拟设备型号后通过。

关闭开关的应用回退保持当前数据库结构与补报接口，不尝试对有收据的数据库破坏性降级；数据库拒绝此类降级的行为已在一次性PostgreSQL验证。恢复特性22/24验收完成，余下5.2长测和5.4全项目覆盖率按用户要求暂缓。百人/百节点容量优化单独由team-scale-api-capacity跟踪。
