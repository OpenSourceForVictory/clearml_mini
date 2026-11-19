# 任务产物基础阶段验证（2026-11-19）

这是 task-result-artifacts 的基础实现检查，不是完整功能上线或 GPU/NPU 真机业务验收。

已实现：兼容 SDK 回合输出目录、任务/回合产物侧栏、按协议声明的回合筛选、历史尝试切换、旧数据库产物的文件限定媒体授权与视频 Range、图片预览和下载。完整设计与待实现任务见 [OpenSpec](../../../openspec/changes/task-result-artifacts/design.md)、[测试矩阵](../../../openspec/changes/task-result-artifacts/test-plan.md)。

## 发现与修正

SDK 新接口按需创建目录，不给未使用该接口的旧业务增加空目录，否则旧 Worker 扫描条目上限可能受影响。Context 逐回合独立；原 output_dir 和旧 artifacts 引用不重写。

第一次完整批次浏览器回归 12 passed / 1 failed：历史尝试选中 old 后初始化 effect 仍用旧闭包写入 new，页面出现“下载 new.json”而非 old.json。改为函数式更新，仅在当前选择仍空时初始化；专项 3 passed，完整 13 passed。之后侧栏文件列表按 run key 重建，避免跨尝试旧数据瞬时复用；专项 2 项重跑。失败定位：frontend/tests/batch-monitoring.spec.ts 原第50行，等待“下载 old.json”超时，保留该触发条件及修复说明而非只报告通过。

## 实际媒体验证

用 FFmpeg 生成 3 秒、160×120 的 WebM，直接登记至隔离 SQLite/API（127.0.0.1:18124）的旧 RunArtifact 数据；真实 Chromium 获取媒体授权、发 Range 请求并播放/seek。结果见 [media-browser.json](media-browser.json)：HTTP 206、duration 3、seek_time 2。无接口 mock，但文件是直接登记用于验证媒体接口，不经过 Worker 上传，不是完整调度或模型运行验收。测试 API 使用临时数据库，退出后清理，当前生产“评测运行”页面不会出现该记录。

## 验证边界

新增目录测试初版 11 passed；SDK/宿主回合/命令适配/协议相关回归初版 105 passed。媒体单测/API初版25 passed。最后扩展测试使用统一覆盖率工具，最终结果见 coverage-final.md 与 regression.txt，不能将不同轮次通过数量相加。前端 13 项批次页回归、TypeScript、Vite 与 OpenSpec strict 通过。Vite 保留 >500 kB bundle 提示；没有提高构建告警阈值。Ruff 使用已有紧凑代码风格对应的 E701/E702 忽略；新增测试的未使用 import 已修正。Python 存在 Starlette/AnyIO 弃用提示。

未完成：回合目录 manifest 自动采集、新大文件持久存储/后台续传、节点自定义目录只读通道、模板配置、文本预览和完整真机验收。旧 Worker 仍受 8 MiB/文件、8 文件/32 MiB 限额。本次没有数据库迁移、没有生产部署或节点升级。目录与业务结果、上传状态的完整分离仍待后续实现；不得按计划文档宣称已经完成。

已同步 docs/task-input.md、docs/batch-monitoring.md、界面用户指南及项目 Skill 参考。

最终关联 Python 回归：142 passed / 2009 deselected，1 个依赖弃用提示，命令使用 PYTEST_ADDOPTS 的关联测试选择并保留全项目覆盖率分母。SDK 行96.43%/分支95.83%，artifact_media 行100%/分支100%；clients.py 行78.11%/分支53.49%，目录和功能门槛仍未达标，覆盖率命令非零退出。没有降低门槛或排除业务文件。真实浏览器/FFmpeg/独立 API 过程未插桩，不计入该覆盖率。

隔离媒体 API 已发送 SIGTERM 停止，临时数据库随进程退出清理；访问凭据只保留于私有 .runtime 文件，未纳入证据或 Git。
