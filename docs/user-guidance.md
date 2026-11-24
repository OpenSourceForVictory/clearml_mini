# 界面用户指南与业务接入

宿主机模板新增“宿主机环境来源”和客户端“回合启动”选项：登录环境读取节点批准业务账号非交互登录 shell 的导出变量，逐回合启动让 runner 在同一执行单元内顺序监视每个前台业务进程。普通命令可显示逐回合完成进度，但没有评测成功率；直接协议逐回合模式需每次输出单回合 final。未选择的新字段不改变已有模板和 Docker 行为。界面入口为运行模板管理、运行向导和“用户指南 → 运行模板 / 业务接入”。定向模拟测试覆盖正常、错误结果、超时及平台变量覆盖；真实 GPU/NPU 冒烟与未覆盖边界见 [宿主机登录环境与逐回合验证](validation/host-shell-episode-launch/README.md)，不代表真实模型评测。

用户入口：登录后左侧“用户指南”；页面内“操作说明”可展开，运行模板还提供“业务如何接入：SDK 与样例”和“参数与目录配置说明”。指南在模态窗口内阅读、搜索和下载，关闭后保留当前表单并恢复触发按钮焦点。成员看不到管理员专用主题。无需访问仓库或外部文档网站。

## 维护映射

内容唯一源为 `frontend/src/guide-content.ts`，呈现为 `user-guide.tsx`；顶层页面在 `main.tsx`，管理分类在 `administration.tsx`，嵌套功能页由 `navigation.tsx` 与 `functionGuideTopics` 显式映射。模板表单、服务实例提供专用入口。

模板“业务参数”需先启用类型化字段并填写字段名称，才会出现在“动态参数映射”的业务参数列表。已有绑定仍可选择，并定位到下方的现有映射编辑，不会复制第二份；模板环境变量直接传给业务程序，不作为业务字段来源。具体操作见[运行模板注册说明](runtime-templates.md)。

注册机器时的共享批准说明与实际表单同步：SSH 纳管在认证字段前显示 GPU/NPU 类型、设备编号和共享选择；手动接入先选择设备类型及共享批准，再生成并下载含对应字段的节点凭据。针对性验证包括 `ssh-onboarding.spec.ts`、`memberships.spec.ts`（mock API 浏览器流程）和系统安装器共享字段用例；这些用例不代表真实 GPU/NPU 节点安装或业务任务验收。完整项目覆盖率本次未运行。

提交预检确认页现列出客户端所需宿主机逻辑环境或容器镜像，以及托管服务的资源池和服务端逐卡阻塞原因。`run-wizard.spec.ts` 使用 mock API 验证显示和建议；`test_preflight.py`、`test_orchestrations.py` 验证只读诊断及节点原因，不代表真实服务部署、GPU/NPU 负载或模型评测。完整覆盖率仍需按项目统一口径另行运行。

客户端环境错误细化：预检逐卡区分业务镜像未批准、节点缺镜像、探测失败、配置未应用或报告过期、宿主机逻辑环境未批准或检查失败，并指向机器管理中的相应配置页。只显示安全归类，不向任务提交者展示 Worker 上报的原始错误、私有路径或凭据。`test_preflight.py` 覆盖正常和失败归类及脱敏边界；`run-wizard.spec.ts` 验证展示，均不等同于真实业务镜像试跑。

运行模板指南和宿主机批准环境页说明：NPU 宿主机任务会以批准的业务账号加载节点安装的 CANN `set_env.sh`，用户无需在每张卡或模板重复填写标准 CANN 库路径；业务 Python 依赖仍需自行准备，Docker 仍使用镜像内环境。定向 Python 回归及前端 TypeScript/Vite 构建结果见 [CANN 环境加载验证](validation/auto-cann-host-environment/README.md)；未在缺少 `torch_npu` 业务环境的 29 节点宣称真实服务验收。

宿主机身份提示：新 SSH 纳管使用纳管账号作为业务账号，root SSH 纳管即为 root 业务；已有节点保留原身份。界面模板和批准环境 JSON 不提供身份切换。root 可读取本机敏感文件；业务仅在模板选择登录环境时读取该账号的导出变量，NFS root_squash 可能继续拒绝访问。新纳管填写工作目录根路径后，安装计划逐卡显示 `根路径/机器名/设备名` 作为独立工作目录，资产、缓存使用机器目录下的 `assets`、`cache`，同机各卡共享。旧版本 root 手工批准的验证范围见 [root 业务身份验证](validation/opt-in-root-host-business/README.md)。

宿主机环境指南说明模板业务变量和允许覆盖列表的边界：设备可见变量与 `MINI_EVAL_*` 由平台保留，Worker 启动前再次拒绝异常任务覆盖，分配的 GPU UUID 在业务变量合并后写入；这不是 GPU 硬隔离。定向测试及失败边界见 [设备环境验证](validation/host-device-environment-boundary/README.md)。

宿主机环境指南补充平台选卡后本机身份复核：物理 GPU 的任务编号和 UUID 必须与 Worker 当前查询结果一致；不一致时按机器设备和每卡配置排查。定向测试只模拟设备查询及启动，不代表真实多卡运行，见 [GPU 分配验证](validation/host-gpu-assignment-verification/README.md)。

任务详情指南补充执行单元缺失提示：宿主机快速退出且已有可信退出记录时继续按退出码完成；缺少记录时拒绝重启原执行，并在错误中区分 host unit 与 Docker container。用户应先看更早的启动日志，修复首次错误后从平台发起新执行尝试。定向恢复测试仅模拟 systemd/Docker 状态，不代表现场业务程序验收。

节点报告有效期调整：宿主机环境、容器镜像/资产和存储回执统一为 60 秒；GPU/NPU、主机 CPU/内存和磁盘采样为 45 秒。提交向导指南说明两类时间窗口。Worker 离线和执行租约仍分别判断，未来时间戳、配置版本不匹配或探测失败不会被延长窗口掩盖。`test_freshness.py` 与相关资源/存储测试覆盖边界；本次不以模拟采样声称真实节点长时间稳定运行，也未运行全量覆盖率。

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

## 业务程序接入上手路径

用户登录后从左侧“用户指南 → 业务接入”下载 `mini-eval-sdk.zip`，在解压目录运行 `python3 self_test.py`。应用内的“业务接入”“SDK 对接”“直接评测协议”主题分别解释模式选择、Python 类 SDK 与非 Python JSON-lines 协议；ZIP 中的[完整操作说明和样例](../examples/integration/README.md)提供界面字段、模板设置、输入结构、命令及结果格式和常见错误。

接入说明将任务类型、协议方式、运行位置和逐回合启动分开解释；普通命令退出 0 只代表进程正常结束，评测成功率来自 SDK/直接协议的有效业务结果。ZIP 自检使用模拟业务，不调用模型、GPU/NPU、仿真器或现场服务。

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

## 登录会话

指南主题 `login` 说明真实操作续期、闲置/绝对时限、到期提示和同账号恢复。具体接口与限制见 [登录会话](login-sessions.md)。过期会隐藏并保留当前页面，主动退出才清空；刷新或关闭页面不保证保存草稿。

机器管理概览和表格使用“加速设备总数/数量”，包含 GPU/NPU。此修正适用于客户端和服务端角色；计数不等同于本次纳管的版本及回连验收。

机器管理指南新增“删除机器”和“清除节点残留”步骤：区分平台登记与远端安装、备份恢复位置、待清理/升级阻止条件、标准 root 安装边界及控制面 401 排查。页面内帮助与 docs/node-reenrollment.md 同步；浏览器回归覆盖 SSH 信息、确认计划、备份显示、删除取消及列表消失，未执行用户真机清理。

计算资源指南增加全部、客户端、服务端分类与双角色去重说明；普通用户仍只看计数，管理员查看角色明细。验证见 [资源角色视图记录](validation/resource-role-views/README.md)。

机器指南新增后台探测依赖、300 秒回连窗口和“继续回连验收”说明，详见 [节点重新纳管](node-reenrollment.md#回连超时与可选探测依赖)。

版本升级帮助明确节点组件包覆盖 Worker、Runner 和已配置托管的 Agent，区分平台容器适配器与旧镜像内置 Runner；界面和升级操作用例见 worker-upgrades.spec.ts。

四角色 API 隔离的管理员说明见用户指南“API 通道隔离与过载排查”与 [部署步骤](priority-api-isolation.md)，包含角色职责、连接预算、健康检查及租约限制。普通用户不显示该运维主题；`frontend/tests/user-guide.spec.ts` 检查管理员搜索入口、普通用户过滤和查看帮助不提交配置。
