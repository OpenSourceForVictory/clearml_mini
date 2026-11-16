# 通用业务命令验收（2026-11-17）

OpenSpec：generic-business-commands。代码版本目标 Worker 0.17.0 / 协议 14。本次未发布 API、前端或升级节点。

## 结果

- 新功能矩阵：50 passed（24 个宿主机实际进程组合、24 个真实 Docker 组合、2 个解析/契约测试）。Python 带选项、Bash 解释器、shebang、相对原生二进制、PATH 原生二进制、显式 Bash shell × 普通命令/直接评测/SDK 回合/服务入口。
- 公共解析模块 `runner/process_command.py`：23 条语句、10 个分支，覆盖率 100%；仅此模块，不是全系统覆盖率。
- 界面 Playwright：14 passed，覆盖客户端/服务端、模板查看/复制/保存、通用模式、独立 Docker cwd 与平台 Python、指南示例、参数及目录草稿。
- 隔离关联回归：119 passed、12 skipped，包含宿主机、镜像、参数、任务输入、SDK、调度与升级模块；跳过项需要 systemd/特权 opt-in。最终版本准入条件的小幅补充另由新功能契约测试与 UI 全组验证。
- TypeScript 检查和 Vite production build 通过；产物存在大于 500kB 的 chunk 提示。

## 失败证据与处理

1. 修改前的宿主机真实子进程：`python3 -u app.py` 默认 work 下找不到 app.py，`/bin/bash run.sh` 被批准根目录检查拒绝。新增 exec 语义通过；legacy 保留原行为，迁移须选择新模式。
2. 首轮 Docker 测试：24 项因测试镜像默认用户无法读取测试临时目录失败，并发现镜像默认 cwd 会优先导入旧 runner。测试显式设置当前 UID/GID、`/platform` cwd 和只读 runner 挂载后解决，不修改镜像或生产数据权限。
3. 第二轮 Docker 测试：6 个 SDK 组合因测试构造未补 API 默认 assets 字段失败；通过正式 runtime validator 生成配置后，50 项全部通过。
4. 工作区关联回归发现已有未提交 N:N 集合改动：`backend/app/batches.py` 中 BatchSubmit.unique_cases 访问不存在的 template_ids，触发 AttributeError。本次没有修改或提交该改动；以 Git HEAD 加本特性文件创建隔离目录复验，原工作区文件完整保留。

## 验证边界

- Docker 容器无网络、无额外 capabilities、有限 CPU/内存；业务为确定性测试，原生程序由本机 cc 编译。
- 宿主机矩阵使用真实子进程，模拟环境 probe；不是新的 systemd/物理 GPU/NPU 纳管验收。已有 systemd/特权测试需要明确 opt-in。
- 服务入口样例执行后退出，本次未重新进行长驻 WS/推理服务就绪、租约、并发排队的全链路真机验收。
- 不承诺任意架构二进制可运行；解释器、动态库、权限、业务数据需要预先准备。

运行命令见 [通用命令指南](../../generic-commands.md)。

后续全量覆盖率测试发现两个遗漏并修复：API Register 白名单未包含协议 14 导致节点注册 422；SDK 下载包未重新生成且构建清单遗漏 process_command。补充当前协议注册断言与包内模块断言，更新可下载 ZIP。原 119 项关联回归不足以发现这两个问题，因此不能将其视为全量验收。
