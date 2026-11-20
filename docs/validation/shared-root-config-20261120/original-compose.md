# 原部署命令兼容验证

用户要求保持原有构建和启动命令不变。本轮将可选环境变量及只读挂载内置到 deploy/compose.yml，并保留旧 overlay；未配置变量时挂载仓库占位目录，API映射仍关闭。deploy/example.env、共享产物操作指南、界面指南及Skill索引同步更新。

针对性回归30项通过，0跳过：test_shared_storage_compose.py、test_shared_root_config.py、test_bundled_onboarding.py。新增8项实际Compose解析测试覆盖未设置/空变量、配置目录（含空格）、高级配置及[]优先、旧overlay不重复挂载、缺目录不自动创建、其他服务及构建配置不变。前端类型检查及OpenSpec严格校验通过。

实际Docker验证从主compose渲染结果提取挂载与环境：未设置变量时非root账号启动且映射为空；设置目录时无marker读取成功并拒绝写入；目录不存在时Docker拒绝启动且未创建目录。使用独立临时目录、当前源码和已有API依赖镜像，不更新生产容器或实际业务。结果见 original-compose-docker.json。

本轮未修改Python生产代码，未重新运行全量测试或全量覆盖率；不能以30项通过代替全仓覆盖率。上一轮选择性覆盖率及未达门槛证据保留。没有新增数据库迁移，无需因此升级Worker。未部署生产。
