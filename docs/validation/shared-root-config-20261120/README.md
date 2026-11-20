# 单变量共享产物配置验证

实现 simplify-shared-artifact-root。未部署生产，未升级节点，无数据库迁移。

相关回归：54 passed、1 skipped。命令为 coverage run --rcfile=backend/tests/project.coveragerc -m pytest backend/tests/test_shared_root_config.py backend/tests/test_shared_artifacts.py backend/tests/test_shared_artifact_migration.py backend/tests/test_execution.py -q --tb=short。跳过项为未配置专用PostgreSQL的迁移集成测试；本次未修改数据库模型。前端 tsc --noEmit 与 OpenSpec strict 校验通过。

新增13例覆盖：配置身份、根撤销、非法路径、根外路径、显式配置优先、空数组关闭、非法JSON不降级、外部不能注入automatic、无marker读取、链接拒绝、缺目录、任务领取shared标记、重复登记与跨用户/路径鉴权。

初轮13例有2项测试夹具失败：NUL无法写入操作系统环境变量；登记路径未使用执行ID后缀。分别改为换行非法变量值与真实执行ID，并修正浏览路由后通过。未降低生产校验规则。

实际Docker使用当前API依赖镜像、当前源码只读挂载、UID/GID65534和临时真实目录，验证只设置总目录的Compose渲染、无marker列表读取及只读写入拒绝。该实验没有启动业务模型，不是远端NFS验收；临时容器和目录已清理。

覆盖率沿用 scripts.test_coverage 的project.coveragerc与scripts.coverage_report口径，使用完整Python源码分母。共享映射模块行94.92%/分支87.50%，共享文件I/O行95.24%/分支100%；execution整体77.89%/53.75%。选择性回归不满足全仓目录及功能组门槛，summary.md保留全部失败门槛；没有宣称全量覆盖率通过，未插桩前端和Docker独立进程未并入覆盖率。

高级JSON已有历史配置保持优先。简化模式没有磁盘身份marker，需管理员确保API与节点访问同一份文件；不同路径继续使用高级配置。旧登记不自动改绑，新功能不解决远端本地盘读取。
