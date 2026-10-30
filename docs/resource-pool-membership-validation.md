# 资源池成员管理验收（2026-10-30）

## 自动验证

- 后端、Worker、安装器及 PostgreSQL 集成：87 通过，1 个 Docker 探针用例在全量运行时未指定镜像而跳过；随后指定 mini-eval-runner:robotwin-render 运行存储 Worker 测试，6/6 通过，补验该探针。
- 浏览器：5/5 通过，包含无池配置下载、真实设备默认值、接入地址、按机器选当前显示卡、轮询保留选择及版本冲突、账号/客户端入口、原存储配置与 CS 页签回归。
- TypeScript 检查、Vite 生产构建通过，OpenSpec strict 校验通过。
- PostgreSQL 专用迁移库验证：旧归属回填、可空实际池、期望池外键、未分配/未排空时拒绝降级、合法降级及再次升级。
- 并发测试同时提交两个成员变更、存储变更和 claim，确认版本冲突整体拒绝；已领取执行保留，否则预留退回 PENDING，资源记账清零。
- 双槽位测试验证同机跨池 CPU/内存合计和成员/存储同时阻塞；离线遗留清理和失效目标不会提前迁移。

## 两台真实宿主机上的隔离协议验收

本机 RTX 4090 主机与 R8000 主机，各启动专用模拟身份 Worker。使用隔离 API 和 PostgreSQL 库，原生产身份及归属不参与修改。

依次验证：无池接入 → 加入池 → 运行中换池 → 等待完成并迁移 → 移出池 → 两台 Worker 携带旧 pool_id 重启 → 仍未分配 → 再次加入并执行。

4 个 Docker 客户端任务、8 个回合全部成功；两台主机均实际执行，记录逐回合结果、运行身份和成员版本。证据：[pool-membership-20261030.json](validation/pool-membership-20261030.json)。

这是客户端框架及调度协议测试，不是真实模型评测；两台单卡主机不能替代真实多卡主机硬件验收。多卡安装和跨池额度已用确定性测试覆盖。

## 正式部署

API/前端已部署，数据库 head=d407e09。本机与 R8000 Worker 已加载新代码并恢复在线。真实管理员登录验证四个子页签、机器目录入口和池详情，两张卡均基础就绪，原 gpu-pool 归属不变。两台机器的安装器 --check 均通过身份、GPU、Docker 和镜像检查。

发布目录：/home/huawei/clearml_mini/.runtime/membership-release-20261030-182335/。目录内 database-before.dump 为迁移前数据库备份，previous-code.tar.gz 为旧代码；回退镜像 mini-eval-api:before-membership-release-20261030-182335。本机旧代码备份 .runtime/membership-local-code-before.tar。

部署证据：[pool-membership-deployment-20261030.json](validation/pool-membership-deployment-20261030.json)。截图保存在本机 .runtime/membership-live-ui.png。正式库未通过移池做验收；独立测试 Worker、SSH 测试隧道、测试 API、Vite 和临时 PostgreSQL 均已停止，测试凭据已停用。测试库导出保存在 .runtime/membership-test-databases.sql（600 权限）。

回退前按 [成员管理指南](resource-pool-membership.md) 处理未分配设备和排空。恢复旧镜像及宿主代码时保留执行记录，不在有新数据时直接覆盖数据库备份。
