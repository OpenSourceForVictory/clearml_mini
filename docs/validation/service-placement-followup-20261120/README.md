# 服务管理位置与NPU请求补全

当前API已返回实际placement，但管理设置的服务列表遗漏渲染；复用ExecutionPlacement显示实际节点和GPU/NPU卡号，null及旧API缺字段明确提示，不从target slot推测。部署表单设备名称改为加速卡，并按设备类型标注。

复查发现无资源契约旧模板的NPU部署表单不传accelerator_type，后端缺省GPU会拒绝NPU卡。补充所选slot设备类型；带资源契约模板保持原契约，不自动改写其要求。

修复前请求断言2失败（字段缺失）；修复后6项服务管理浏览器用例通过，涵盖旧GPU字段兼容、NPU请求、同名服务历史日志、就绪说明、实际/未分配/旧API位置。无需数据库迁移或更新Worker。真实界面NPU部署验证已完成，见下文。

真实Chromium从管理设置表单部署29的NPU2服务，确认请求包含accelerator_type=npu，实际服务HEALTHY、页面显示ascend910b3-29 / NPU 2且NPU运算日志可见，再从界面停止并等待清理确认。实例ID及任务ID见ui-evidence.json；未改节点账号或Worker，模板已恢复停用。API前端镜像mini-eval-api:completion-20261120（3b376b8），数据库仍c952f34。

真实页面截图复核发现长节点名挤压列宽；实例表改为固定可读列宽和表格内横向滚动，已停止实例历史错误折叠保留。6项专项浏览器测试通过，包含390px视口无整页溢出、位置列宽及历史诊断默认收起，TypeScript和构建通过（保留已有bundle大小警告）。
