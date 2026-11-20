# 29 节点最终 NPU 服务复验

2026-11-20，当前生产API（迁移c952f34）实际创建两个同名宿主机服务，固定29节点物理NPU2、3。未升级远端Worker、未改账号/环境/设备配置，使用已批准ascend_cann环境。

两实例分别获得不同执行ID、systemd单元及端口；真实torch_npu在逻辑设备0计算sum(arange(16)^2)=1240。实际业务UID1001，设备白名单分别包含davinci2/davinci3，不能把此次测试描述为root业务或模型评测。

每个服务先返回503约15秒，再返回200；实际状态PENDING→STARTING→HEALTHY。上传日志含NPU运算、warming和ready标记。通过管理API停止后两个实例均STOPPED、cleanup_confirmed=true，节点held_executions恢复0；测试模板已停用，服务记录保留供管理页面查看。未生成NPU产物、不要求NFS映射。

具体服务、任务、执行、systemd身份和运算结果见evidence.json。此次是当前API与既有Worker兼容、NPU宿主服务启动/就绪/日志/清理复验；不替代上一轮由平台编排双实例再驱动客户端的完整链路，也不代表真实RoboTwin模型评测。
