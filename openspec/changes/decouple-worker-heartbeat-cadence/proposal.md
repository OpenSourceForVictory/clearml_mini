# Why
Worker基础心跳与领取循环均按2秒运行，重复配置回执会放大共享Worker行锁。需要降低常态上报，同时保持领取和执行控制及时。
# What Changes
基础心跳默认5秒并错峰；完整回执变化时发送、20秒兜底；领取独立维持最多2秒常态轮询。执行控制2秒、本地监测0.5秒和结果上报不变。参数保守有界，不修改服务端失联规则。
# Capabilities
## New Capabilities
- worker-heartbeat: 分离基础心跳和领取周期
# Impact
worker/client.py、新增周期管理模块及测试；运维指南、项目Skill；升级Worker生效，无数据库迁移，旧API兼容。
