# 运行配置动态映射展示验证

触发条件：模板在 parameter_schema.fields 中设置业务绑定，旧运行配置页只读取 platform_bindings，遗漏业务绑定；平台绑定也只在折叠 JSON 中显示。修正为客户端/服务定义共用映射表，保留基础命令参数映射前标签，不声称是实际 Worker argv。

新增 5 个 Playwright 用例，原有 8 项一并运行：13 passed（4.0 秒）。覆盖宿主机/容器客户端、task.task_name 与业务字段同时绑定、argv/env/input、false/0、flag/repeat/json 编码、可选值缺失、任务切换、仅业务字段无平台绑定、服务端未记录端口、旧元数据缺失和显式空映射、小屏。读取失败重试及乱序响应由原有用例覆盖。

命令：在 frontend 下执行 EVAL_API_URL=http://127.0.0.1:18123 node node_modules/@playwright/test/cli.js test tests/batch-run-configuration.spec.ts；使用本地 Vite 和模拟 API 快照。TypeScript --noEmit、Vite build 通过；构建保留大包提示。未运行全量测试，未对前端插桩，不能宣称覆盖率达标或真机传参已验收。无 Python 改动，不生成新的 Python 覆盖率报告。

失败证据：before.txt 为修复前映射表缺失；first-regression.txt 的两项失败是测试未清空进入批次页时的请求历史，把原页面模板列表请求误判成配置页请求，改为从打开运行配置前开始统计，保留不查询实时模板的断言。首轮新增测试还有一个对象闭合符语法错误，修正后才执行 before.txt 的业务断言。

同步用户指南、docs/batch-monitoring.md、Skill project-map 和 OpenSpec batch-run-configuration。仅前端修正，无数据库迁移或 Worker 升级；此次未部署。
