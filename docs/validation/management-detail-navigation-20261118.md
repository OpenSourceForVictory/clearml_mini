# 管理详情导航专项验证（2026-11-18）

## 行为

原机器、运行模板和集合详情追加在列表之后，长列表需要跳到底部找表单。现在点击查看配置切换独立详情，固定名称与返回入口。返回恢复列表筛选、分页和窗口滚动/触发焦点；机器与集合表单保持挂载，重开保留草稿，覆盖草稿的对象切换提示确认。模板只读与复制新版本规则保持。集合新建草稿提供继续查看入口。

范围是当前页面组件内状态，不承诺刷新、关闭或离开整个管理模块后持久化。未修改后端、数据库、Worker 或部署状态。

## 证据

首轮 28 项：27 通过、1 失败。失败为 administration.spec.ts 仍要求外层 region 获得焦点；新导航将焦点放在固定标题区，更新断言并先返回列表再切换机器角色后，第二轮 30 项全部通过。

新增用例覆盖桌面 1440 与手机 390 宽度、30 台机器长列表返回滚动和焦点、筛选保留、机器保存 500 错误保留输入、拒绝切换保留草稿、集合返回重开草稿、客户端与服务端模板独立详情。随后回归节点删除/重新纳管/升级、网络、宿主机目录与容器环境设置：32 项全部通过，无跳过。

最终命令：`playwright test tests/detail-navigation.spec.ts tests/administration.spec.ts tests/suite-layout.spec.ts tests/template-guidance.spec.ts tests/node-removal.spec.ts tests/node-enrollment.spec.ts tests/worker-upgrades.spec.ts tests/service-network.spec.ts tests/host-directory.spec.ts tests/image-runtime.spec.ts`。

TypeScript 检查、生产构建和 OpenSpec 严格校验通过。构建仍有大于 500 kB 的分包提示，未提高阈值掩盖。首次启动 Vite 及一次测试命令从仓库根目录引用 node_modules 导致 MODULE_NOT_FOUND，改用 frontend 工作目录后运行。

仅专项浏览器测试，接口为模拟数据，不是线上或真实硬件验收。未运行全量 Python 覆盖率；前端未插桩，不把通过数量当作行/分支覆盖率达标证据。未部署。
