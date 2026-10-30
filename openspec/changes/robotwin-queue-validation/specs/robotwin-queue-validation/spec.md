## ADDED Requirements
### Requirement: 真实环境检查客户端
客户端 SHALL 使用真实 RoboTwin 场景初始化与三路相机检查，上报每项完成情况，明确结果不是模型表现；不稳定 seed MAY 有界重试并记录实际 seed，其他错误 SHALL 失败。
#### Scenario: 三任务集合
- **WHEN** 运行包含 adjust_bottle、beat_block_hammer、click_bell 的集合
- **THEN** 每项启动独立仿真客户端，报告其任务名、实际 seed 和相机检查结果
### Requirement: 双机排队验收
验证 SHALL 在两台物理 GPU 主机执行三个三任务批次，保存状态时间线及实际执行节点，并检查并发与排队接续。
#### Scenario: 九项竞争两张 GPU
- **WHEN** 三个批次连续提交到同一资源池
- **THEN** 两台 Worker 可并发执行，超出当前容量的任务排队，并在资源释放后自动执行；不得把模拟结果当作现场证据
