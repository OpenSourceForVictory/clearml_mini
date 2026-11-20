# 产物配置与浏览界面发布验证

已发布代码截至2f41135，API镜像mini-eval-api:artifact-ui-20261120。基于现有部署镜像更新共享映射、文件I/O、任务共享标记、执行目录元数据四个API模块及前端；Worker/Runner/SDK安装包仍为原部署版本，未包含另一个待发布0.21.12回合manifest变更。无数据库迁移、无节点升级。

保留原Compose全部覆盖文件、监听、认证配置、SSH纳管socket以及已有artifact-validation映射和/shared/artifacts只读挂载。主Compose另增加未配置单变量时的只读占位挂载，未替换旧映射。每次重建前均确认六节点held_executions=0，暂停新接单，API健康后恢复原启用状态；最终均enabled=true且held=0。API四个模块摘要与仓库核对一致。

真实浏览器访问本机部署的批次 f0517199-0ec0-42d0-9d73-b4e398988232（功能验证｜真实CUDA与SDK回合产物｜20261119），使用真实API与历史文件，无mock：
- 节点输出路径与执行根展示。
- 共享API进入episodes/2/output，预览cuda-result.json真实结果357389824。
- 返回上一层清除文件预览，手动切换历史/共享后路径仍可见。
- 1440px与390px弹窗居中且不超出视口，Escape关闭恢复入口焦点；无pageerror。
- 发布截图发现全局nav/table样式污染，修复后补充6项浏览器回归全部通过；真实页面再次检查横向导航与auto表格列宽。

本轮不重新运行GPU模型或提交业务任务，仅验证保留的真实CUDA产物读取。此前29项浏览器回归通过，样式补测首轮1失败/5通过、修复后6通过；失败证据见../artifact-source-location-20261120/modal-navigation.md。前端未插桩，未运行最新全量测试，不宣称全仓覆盖率通过。NPU不产生产物，不进行NFS或Worker转发验收。
