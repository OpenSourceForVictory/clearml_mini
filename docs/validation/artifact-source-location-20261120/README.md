# 产物来源与输出路径展示修复

触发条件：共享列表返回enabled=false，前端会立即切到历史上传，用户重新选择共享来源仍被强制切回。现保持共享来源并显示配置提示，允许手动选择历史、切回和刷新恢复。

另增加独立的产物路径展示，复用execution-location API新增output_directory。只读本次执行SharedArtifactRoot/唯一同尝试清单，既不读文件也不检查共享映射，不会把planned转换成registered。batch-v1展示执行根与公共output；未知、多目录歧义、无效路径及执行身份不符不猜测当前路径。沿用任务所有者/管理员鉴权，跨用户404。

Python针对性回归21通过0跳过：test_execution_locations.py、test_shared_root_config.py。执行目录模块行98%、分支93.75%；使用project.coveragerc与coverage_report完整源码分母，选择性测试全仓门槛未通过，summary.md保留报告。

浏览器batch-monitoring.spec.ts共26通过0跳过，包括未启用仍可选择、刷新恢复、失败不切换、历史产物预览、缺记录、计划未确认、读取失败仍显示目录。浏览器API使用模拟响应，未统计前端覆盖率，不代表真实模型或现场挂载验收。类型检查、生产构建及OpenSpec strict通过；保留现有大于500KiB前端包告警。

无数据库迁移，无Worker修改。部署指南、界面帮助及Skill索引同步。代码未部署生产。
