## Context
coverage.py 7.17.3 已用于 root 纳管局部测量，CI 未执行统一覆盖率，生产逻辑分布在六个 Python 目录。
## Goals / Non-Goals
统计 backend/app、worker、node_agent、runner、onboarding、scripts，包括未导入文件；输出目录及关键功能计数、未覆盖行、HTML/XML/JSON。测试通过率与覆盖率分别展示。前端 TS/浏览器未插桩，不虚报成全项目覆盖率；独立进程/容器未自动合并。
## Decisions
使用固定 coverage.py，branch=true、relative_files=true；报告按计数加权汇总，不平均百分比。目录分区互斥；关键功能可重叠，仅作风险定位，不加入总数。用户确认要求：目录行 80% / 分支 70%；关键功能行 90% / 分支 80%，未达标必须失败，不因基线偏低而降低门槛。首次完整测试如实列出缺口，后续用户要求补齐测试达到门槛，因此新增全功能验收任务；需要用户确认的业务设计变化保留明确待决项，其余缺陷修复并复测。CI 测试失败优先返回非零，仍保存诊断报告。基线从 Git HEAD 加本特性隔离验证，保留现有其他未提交改动。
## Risks / Trade-offs
SQLite 单测不等价于 PostgreSQL 并发或真机验收；特权/容器测试跳过如实记录。报告不能代替断言质量。
## Migration Plan
新增一条开发命令，替换 CI Python 测试命令并上传报告，不涉及部署。
## Open Questions
无。

## 2026-11-24 verification refresh
Keep the existing six-directory denominator and gates. Full-suite coverage and any subsequently added tests may be combined only against identical production source; record their separate test results. Release backup/restore tests exercise real temporary backup files while isolating Docker commands, asserting network isolation, migration order, historical count preservation, readiness timeout and cleanup. These tests do not establish real deployment acceptance.
