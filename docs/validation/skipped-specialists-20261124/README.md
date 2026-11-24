# 跳过专项补跑（2026-11-24）

前次全量覆盖报告中的 **132 个跳过用例全部补跑通过**，另有 2 个既有索引迁移回归通过，共 134 项。没有重新运行全量测试；真实容器/API 子进程未插桩，因此不把此次通过数换算成覆盖率提升。

| 专项 | 通过数 | 验证方式 |
| --- | ---: | --- |
| 宿主机/容器、编排、日志、就绪、产物、背压、目录及样例 | 115 | 本机真实 systemd 用户 unit、Docker、业务子进程；730.94 秒 |
| 历史迁移和端点/保留并发 | 7 | 每个文件独立 PostgreSQL 临时库，结束删除 |
| 挂载丢失和保留边界 | 2 | 临时特权容器内 mount namespace，无生产 NFS 操作 |
| 旧 API 兼容 | 2 | Git 4d24178 API 源码、当前 Worker、隔离数据库及真实 host/Docker |
| root Worker 下批准业务账号 | 5 | 独立 systemd 容器内创建 mini-eval 用户，验证权限拒绝及四种执行模式 |
| 纳管执行器部署/中断/重启 | 1 | mini-eval-onboarding-test Compose 项目，独立端口/卷/数据库，SSH 仅探测指纹 |
| 索引迁移及千任务查询回归（额外） | 2 | PostgreSQL 正常升级、无效索引恢复、降级和查询断言 |

逐组结果见 [results.json](results.json)。root 组保留 [原始测试输出](managed.log)；容器内 JUnit 文件未能复制出来，不能声称该组保留了 XML。所有临时容器、纳管项目及新建测试库均已清理；原有测试 PostgreSQL 服务保留。

## 发现和修复

初次数据库专项 4 失败、3 通过，失败证据见 [initial-failures.md](initial-failures.md)。

- 两个迁移测试直接启动 `.venv/bin/alembic`，无法可靠加载仓库模块；改为 `sys.executable -m alembic`，与当前测试解释器一致。
- ab852c3 的降级并发删除索引提前提交。较早端点/保留规则拒绝降级时，版本记录回滚，但索引已删除；仅执行 upgrade head 无法修复。补充 schema check 后真实复现缺失索引，不能通过放宽版本断言掩盖问题。
- 修正：升级保留并发建索引；降级使用事务内 DROP INDEX，在已排空的维护窗口执行，后续保护规则拒绝时一起回滚。回归断言版本不变、索引仍有效、端点及保留数据完整，重新 upgrade/check 成功。

没有新增迁移版本，没有修改调度器或线上数据库。正常升级的数据库无需为此降级；未来执行降级须使用修正后的代码/镜像。

## 环境与证据边界

115 项按前次 skips.json 的精确 nodeid 选取，使用 `PYTEST_ADDOPTS=--import-mode=importlib`、当前用户 XDG_RUNTIME_DIR 和 DBUS_SESSION_BUS_ADDRESS，显式开启相应 Docker/systemd 专项。只使用临时执行 ID/目录。API 边界有故障注入，业务进程和容器是真实运行；逻辑设备和样例不代表 GPU/NPU 模型质量验收。

旧 API 首次只提取 backend，缺少 runner 导致 2 失败；补齐同一版本相关源码后 2 通过。root 容器初次缺 D-Bus，4 失败、1 通过；仅在临时容器安装 D-Bus、创建测试账号并补齐解释器路径后 5 通过。未更改宿主机账号/服务配置。纳管测试不确认 SSH 信任、不认证安装节点；只验证指纹探测及专用执行器恢复。

运行原始日志与私有测试配置位于 `.runtime/skipped-specialists-20261124/`，凭据未提交。115 项中有 2 个依赖/框架警告，未忽略失败或移除断言。没有执行生产部署。
