# 界面用户指南与业务接入

用户入口：登录后左侧“用户指南”；页面内“操作说明”可展开，运行模板还提供“业务如何接入：SDK 与样例”和“参数与目录配置说明”。指南在模态窗口内阅读、搜索和下载，关闭后保留当前表单并恢复触发按钮焦点。成员看不到管理员专用主题。无需访问仓库或外部文档网站。

## 维护映射

内容唯一源为 `frontend/src/guide-content.ts`，呈现为 `user-guide.tsx`；顶层页面在 `main.tsx`，管理分类在 `administration.tsx`，嵌套功能页由 `navigation.tsx` 与 `functionGuideTopics` 显式映射。模板表单、服务实例提供专用入口。

| 界面 | 主题 |
| --- | --- |
| 我的任务、全部任务 | jobs |
| 计算资源 | resources |
| 业务评测向导 | wizard、parameters、services |
| 运行记录及任务集合 | results、suites |
| 客户端镜像、代码、模板版本 | builds、templates |
| 注册/查看模板 | templates、integration、parameters |
| 机器配置各页 | machines、agent、upgrades、host、storage、runtime、retention、network |
| 管理分类 | machines、pools、templates、suites、accounts |
| 服务实例 | services |

每次修改代码必须同步相关文档或 Skill，规则记录在根目录 AGENTS.md。新增功能页须补指南，协议变更须同步可执行样例。Agent 入口为 `.agents/skills/clearml-mini/SKILL.md`，不作为用户操作入口。

## 下载包

`scripts/build_integration_bundle.py` 从 runner 模块与 examples/integration 构建确定性 ZIP 到 `frontend/public/integration/mini-eval-sdk.zip`。静态包只含 SDK、样例与模板，不含部署配置、用户数据和凭据。用户从指南下载后执行 `python self_test.py`，无需访问平台或 GPU；包还包含独立 Dockerfile、HTTP 服务端样例与完整模板，具体使用步骤包含在包内。

普通命令按退出码判断完成；SDK 命令每次一回合，写 MINI_EVAL_RESULT；Python SDK 类方式复用直接协议；其他语言可按逐行 JSON 实现直接协议。自检覆盖成功和业务失败，不是模型质量评测。

## 验证

2026-11-16 验证：

- SDK 下载包解压自检、命令包装及 API 回合契约：22 项通过；补入输入示例后重新验证包一致性与离线执行，1 项通过。
- 界面回归初次 75 项通过、4 项按环境跳过、1 项批次筛选保留失败。该轮运行中进行了前端热更新；停止修改后复查批次监控与指南的 6 项全部通过。未把初次失败隐藏为一次全绿。
- 修复搜索输入拦截 Escape 的问题：帮助窗口可直接 Escape 关闭，恢复触发按钮焦点，保留草稿且不发送写请求。
- TypeScript、Vite 构建、OpenSpec strict 校验及项目 Skill 校验通过。
- 发布 `mini-eval-api:user-guidance-20261116`，上线前确认无持有执行并核对 Compose 环境不变。线上静态资源的 3 项指南浏览器测试通过（API 使用 mock，无生产写操作）；另以实际登录只读检查 API 和下载包字节一致性。

无数据库迁移、无 Worker 升级、无调度语义变更。本次不是全量后端回归，也不是新增 GPU/NPU 或真实模型评测。

旧 root 安装的 SSH 向导显示原程序路径与原位更新说明；机器指南同步可信 updater 布局、全设备检查和恢复记录阻断。浏览器布局提示测试使用模拟 API，不代表真机完整纳管。

机器管理概览和表格使用“加速设备总数/数量”，包含 GPU/NPU。此修正适用于客户端和服务端角色；计数不等同于本次纳管的版本及回连验收。
