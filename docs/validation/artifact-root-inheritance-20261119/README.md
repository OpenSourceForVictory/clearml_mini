# 卡级产物根继承验证

修复触发：卡已经单独设置工作目录，产物根缺省/null时，过去会覆盖掉整机产物根。现在只对此字段继承，显式卡级值优先，不修改原始卡配置；整机改动与清空随存储修订下发。卡编辑器不将报告中的继承值回填为显式覆盖。旧回执即使修订相同，产物根不一致也不放行新任务；重新保存整机配置并确认后恢复。

- Python关联71通过、3跳过、2204未选择；存储页面5通过。backend/app/storage.py行96.69%、分支91.07%。全仓门槛未达标，保留coverage-summary.md；前端未计入Python覆盖率。
- test_storage.py覆盖缺省/null/显式值、两个卡、心跳下发/错误回执、整机变更/清空、原始配置不变、旧版本回执暂停准入与重新保存恢复；关联存储/执行测试验证活动执行与旧配置行为。
- storage.spec.ts新增卡工作目录编辑后仍以null保存产物根的场景；现有轮询、冲突、权限与清空测试一起运行。
- TypeScript、Vite、Ruff和OpenSpec严格检查通过；保留Vite大包提示、Starlette弃用警告。跳过详情见results.json。
- 使用统一工具，PYTEST_ADDOPTS为 `--import-mode=importlib -k "test_storage or worker_storage or artifact_layout or execution_launch_boundaries"`，运行 `.venv/bin/python -m scripts.test_coverage --output 自选目录`，完整分母不裁剪。

本轮未部署、未运行真机继承任务。只需发布API和前端，无新增数据库迁移，已有Worker0.21.10不需重装；已有配置在发布后重新保存整机存储设置并等待逐卡确认。指南和Skill参考已同步。
