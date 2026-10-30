# 集合管理验收（2026-10-30）

- 后端、Worker、安装器、PostgreSQL 与真实 Docker 探针合计 102 项通过，无跳过。
- 浏览器：原 CS 页签、成员管理与存储回归 5 项通过；连接真实隔离 API 的集合生命周期及 YAML 导入 2 项通过。
- 实测 RoboTwin 50 任务目录选 3 项，每项 2 回合，预览与保存为 3 任务 / 6 回合；草稿保存保持页面，切换页签不丢修改；发布后只读。
- 实测创建新版本、删除未引用草稿、归档恢复、JSON 导出再导入后客户端映射与重复提示、YAML 默认值及错误定位。
- 后端验证越权读取/修改、无模板保存、发布与编辑并发版本冲突、旧创建调用兼容、历史运行快照不变、草稿/归档禁止发起运行、分页筛选。
- 专用 PostgreSQL 迁移库验证旧集合 published 回填、草稿/归档阻止降级、合法降级和再升级。
- TypeScript、Vite 构建、OpenSpec strict 校验通过。

隔离验收只验证集合管理和 API 行为，不执行真实模型推理。资产和服务端未准备好仍可保存集合；发布集合不等于真实任务已评测完成。

## 部署

API/前端已部署，正式数据库 head=e518f10；镜像包含 PyYAML 6.0.3 和固定任务目录。真实登录创建三任务六回合草稿并删除，原有两个集合的名称、版本、清单与客户端绑定不变，均迁移为已发布。两台 Worker 在线、基础就绪，协议未变，无需重启。

证据：[suite-management-20261030.json](validation/suite-management-20261030.json)。页面截图位于 .runtime/suite-live-ui.png。

回退目录：/home/huawei/clearml_mini/.runtime/suite-release-20261030-185647/，包含 previous-code.tar.gz 和 database-before.dump；旧镜像 mini-eval-api:before-suite-release-20261030-185647。遵循集合管理指南处理草稿/归档后方可降级，不覆盖新任务数据。

临时 API、Vite 和测试 PostgreSQL 已停止并删除容器，测试数据库导出保存在 .runtime/suite-test-databases.sql（600 权限）。正式测试草稿已删除，没有提交正式评测任务。
