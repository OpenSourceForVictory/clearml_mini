# 两端分配位置验证（2026-11-18）

原实现：客户端位置位于状态列小字且硬编码 GPU；服务端只有槽位 ID/地址，默认摘要与详情不展示节点及卡号。新增 placement 使用最新 Run.slot_id，避免配置目标或重试旧尝试被当成实际位置。批次批量读取槽位，不按任务增加查询。

专项后端：test_execution_placement.py、test_monitoring.py、test_services.py 合计 22 passed，1 条 Starlette 弃用警告。覆盖 GPU/NPU、未分配、软删除历史槽位、最新尝试无分配、服务目标不能代替实际分配以及既有权限和监测查询数量约束。

浏览器：batch-monitoring.spec.ts、orchestration-status.spec.ts 共 7 passed；补强默认摘要断言后单独重跑 orchestration-status.spec.ts 2 passed。覆盖客户端独立位置列及 GPU 0、服务端默认摘要与关联详情 NPU 3、未分配提示、既有筛选与重试选择保持。TypeScript、OpenSpec strict、git diff --check 通过。无失败或跳过用例。

本次未运行全量、统一覆盖率统计或真机业务；浏览器使用模拟 API，不能作为真实 GPU/NPU 调度验收，不宣称覆盖率达标。未部署。需同步发布平台 API 和前端，无需数据库迁移或 Worker 更新。
