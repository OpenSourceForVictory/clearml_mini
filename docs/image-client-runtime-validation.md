# 镜像客户端与目录映射验收（2026-10-30）

## 自动验证

- 完整后端/Worker/PostgreSQL/真实 Docker 探针回归 107 项通过，无跳过；随后新增的 3 项测试及受影响测试定向通过，总计 110 个不同测试。
- 7 项浏览器测试通过，覆盖 CS 页签、机器成员/存储、新镜像注册、变量覆盖、草稿保留、节点版本冲突；新表单标签修正后对应 2 项再次通过。
- 迁移独立 PostgreSQL 库：保留旧模板、阻止带新模板或节点配置降级、允许无新配置降级并再升级。
- PostgreSQL 并发：两个管理员同时保存只有一个成功；配置/存储修改与领取并发时，要么保留已启动执行，要么释放预留及额度。
- 验证私有模板权限、保留环境变量、占位符只替换一次且不做 shell 求值、目录交叠限制、旧协议禁止新模板、过期报告拒绝领取、单项资源缺失不阻断其他兼容任务、镜像恢复重试、执行快照持久化。
- TypeScript、Vite 构建、OpenSpec strict 校验通过。

## 双机真实容器

使用本机和 R8000 的隔离模拟 Worker、独立资源池和测试 API，不占用正式 GPU 资源。镜像只有 Python 和业务示例，移除内置 runner，设置默认 ENTRYPOINT=/bin/false；平台覆盖入口、注入适配层后执行。

8 个任务、16 个回合全部成功，且全部只有一次执行尝试。验证：

- 不同宿主资产路径映射到相同 `/data/robotwin`，实际 Docker inspect 确认只读。
- `/workspace/eval`、`/results` 映射到各执行独立的 work/output，日志、回合、指标、JSON 产物完成上传。
- 默认变量/覆盖值和任务名传给客户端；实际加载的 SDK 来自注入目录。
- 资产缺失时任务保持 PENDING，管理员修复后自动执行。
- 运行中修改映射等待清理，旧任务仍读旧目录，新任务读新目录。
- 杀掉并恢复隔离本机 Worker 后，原 run_id 和原目录快照不变；恢复本机配置后再次成功。

这是执行协议和资源映射验收，不是 RoboTwin/Cosmos 模型质量评测。测试证据见 [image-runtime-20261030.json](validation/image-runtime-20261030.json)。

## 正式部署

API/前端已部署，数据库 head=f629a11；本机 local-4090、R8000 的 Worker 均为 v5、在线且批准镜像探测就绪。现有两个集合及节点配置与部署前一致，未修改正式节点目录或提交正式评测任务。

真实登录浏览器验证了镜像注册表单、变量覆盖入口、草稿保留、双机镜像与资产报告，无页面错误；未创建正式测试模板。截图 `.runtime/runtime-client-ui.png`、`.runtime/runtime-nodes-ui.png`。

回退材料位于 R8000 `/home/huawei/clearml_mini/.runtime/runtime-release-20261030-225549/`：`previous-code.tar.gz`、`database-before.dump`；界面标签修正前另有 `database-before-ui.dump`。原 API 镜像 `mini-eval-api:before-runtime-release-20261030-225549`。本机旧 Worker 源码 `.runtime/runtime-local-before.tar.gz`。按[使用指南](image-client-runtime.md)先停止新模板使用并排空执行，再评估回退；不能覆盖上线后产生的数据。

隔离 Worker 已停用、直连 SSH 测试隧道/测试服务已停止，临时 API、Vite、PostgreSQL 已清理。测试数据库备份 `.runtime/runtime-test-databases.sql` 权限 600，仅用于追溯。
