# Tasks
## 1. 实施与同步
- [x] 1.1 实现默认映射、共享传输判断和无marker读取，补正常、失败、兼容与隔离测试；同步Compose、指南和Skill索引。
## 2. 验证
- [x] 2.1 运行共享产物与执行相关回归、覆盖率报告及Compose渲染验证；记录边界并严格验证OpenSpec、提交。

证据：docs/validation/shared-root-config-20261120/README.md。选择性回归54通过1跳过；全仓覆盖率门槛未达，不是全量验收。未部署。

## 3. 原命令兼容
- [x] 3.1 将可选挂载集成到主Compose，覆盖未配置/已配置/缺目录/旧overlay合并与高级映射场景，更新操作文档、界面指南及Skill；执行相关测试和实际容器验证后记录结果并提交。

3.1证据：docs/validation/shared-root-config-20261120/original-compose.md；30通过、0跳过及实际Docker验证通过，未部署。
