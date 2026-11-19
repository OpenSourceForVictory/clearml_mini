# 通用产物预览阶段验证（2026-11-19）

## 范围与边界

本阶段实现已有产物接口上的 JSON 格式化/懒展开、TXT/LOG 文本、CSV 表格、WebP 图片支持。明确限定文本 256 KiB、CSV 200 行/50 列、JSON 每层 50 项/12 层；切换取消旧读取，错误支持重新授权重试。HTML/SVG 不内联执行。

OpenSpec 同步用户已确认的新方向：不主动上传新文件，不建集中产物卷，仅节点索引和按需读取。但 Worker/API 节点通道、Docker 持久输出挂载、自定义目录与自动回合归属尚未接通；当前实现仍读旧上传正文。未部署、未升级 Worker、无新增数据库迁移。测试没有模拟成 GPU/NPU 真机业务验收。

## 验证

- `pytest --import-mode=importlib backend/tests/test_artifact_media.py -q`：29 passed，1 条既有 Starlette/AnyIO 弃用警告。
- `PYTEST_ADDOPTS='--import-mode=importlib -k artifact_media' .venv/bin/python -m scripts.test_coverage --output coverage-artifacts/artifact-preview-20261119`：29 passed，2123 deselected，1 warning；统一门槛退出 1，完整目录统计见 coverage.md。修改的 Python 模块 artifact_media.py 为行 83/83、分支 26/26（100%/100%）；不代表目录或功能组达标。
- Chromium `batch-monitoring.spec.ts` 全文件：19 passed（35.4 秒），其中 8 项产物相关交互；使用模拟 API，验证 CSV 引号/换行/限制、JSON 折叠/无效输入/纯文本防注入、忽略 Range 的超长返回/截断、空文件 416、UTF-8、授权失败与重试、切文件取消与历史隔离。不是节点端到端验证，前端未插桩，不宣称前端覆盖率。
- TypeScript、Vite、Ruff、OpenSpec strict、git diff --check 通过。Vite 仍提示主包超过 500 kB（602.57 kB）；未提高警告门槛。

本阶段没有新增失败用例记录；统一覆盖率门槛未通过的证据保留，不通过排除未测试业务代码规避。完整节点功能及真机验收维持 OpenSpec 未完成项。
