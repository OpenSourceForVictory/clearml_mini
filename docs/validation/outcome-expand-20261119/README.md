# 列表与任务行展开入口修正

之前仅 ResultOutcomes 顶部统计卡带详情，列表/任务行直接使用的 ExecutionOutcome、ProtocolOutcome 只有摘要。现将原生 details 移入两个共享组件，所有调用处均可独立展开、收起；无上报数据时也有说明。统计口径、逐回合日志入口和后台接口不变。

浏览器初轮 24 passed/1 failed：新增任务行测试用 exact case0 匹配包含嵌套 task_name 的单元格，定位失败；改为用当前任务补跑复选框定位行后单项通过。保留失败与复测证据。测试覆盖列表和任务行独立展开、折叠、轮询保持、顶部卡片不串状态、窄屏边界。TypeScript、Vite 构建、OpenSpec 严格校验通过，构建保留大 chunk 提示。前端未插桩，不宣称覆盖率；模拟 API 的浏览器检查不是设备业务验收。

本机 18000 已原子替换静态 index，healthz=200；未重启 API、未修改数据库或节点。持久镜像 mini-eval-api:outcome-expand-20261119 基于 outcome-summary，仅更新 dist；既有覆盖文件已更新，旧 index 和覆盖文件备份位于 .runtime/outcome-expand-release-20261119。其他机器未发布。生产构建不运行依赖 /src/api.ts 的开发专用测试，该项开发测试已通过。

正式入口关联复测 24 passed，包括修正后的任务行定位与所有行内展开测试。
