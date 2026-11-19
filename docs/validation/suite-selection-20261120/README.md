# 集合全选验收

触发：搜索过滤后，原界面只能选择显示任务，无法直接选择集合全部任务。现在增加独立全选及隐藏选中数量，保留当前显示选择的替换语义。

浏览器专项 6 通过，包含 50 项批量修改实际表单结果、搜索无结果、替换选择、清空、保留任务后索引重置、已发布和归档只读。TypeScript 检查通过。测试使用模拟 API，不是后端保存或真机运行验收；前端未插桩，无代码覆盖率结论。无数据库或 Worker 修改，尚未部署。

命令：`EVAL_API_URL=http://127.0.0.1:18081 node node_modules/@playwright/test/cli.js test suite-selection.spec.ts suite-layout.spec.ts`（frontend 目录）。
