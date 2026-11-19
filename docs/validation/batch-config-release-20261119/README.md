# 批次配置展示及再次运行接口补发（2026-11-19）

## 触发与修正
运行配置此前仅显示基础 argv 和映射表，不能直接核对拼接结果。现从既有提交配置按 Runner 顺序展开命令，并独立展示合并显式环境；未知分配值保留占位。用户取消实际启动快照扩展，相关未完成 API/DB/Worker/Runner 代码已撤回，没有执行新增迁移。

本机运行 API 未注册 rerun-draft，静态前端已有入口，因而通用 Not Found。此发布在现有 API 镜像上仅补入仓库已有 batch_rerun.py 及 main.py 注册（静态路由之前），并更新前端。没有发布其他未部署后端功能，也没有升级节点。

## 验证
- 现有导入 API 回归 6 passed；新增/关联浏览器 19 passed，部署后的静态前端同样 19 passed。浏览器使用模拟接口，不是模型验收。
- 候选镜像及上线容器读取真实数据库的 10 个历史批次，草稿均返回 200；使用临时进程 TestClient 管理员依赖替身，只读访问，不启动任务或修改源批次。此项不是实际浏览器登录验收。
- 真实 HTTP healthz=200；未登录访问 rerun-draft=401，接口不再落入静态 404。
- TypeScript、Vite 和 OpenSpec strict 通过；Vite 仍有超过 500 kB 的 chunk 警告。
- 首轮 1 failed/18 passed：新增折叠区含相同环境文本，旧测试匹配到隐藏项，改为限定已展开区域。第二轮 1 failed/18 passed：详情跳转前定位了列表的按钮，增加详情区域就绪断言并限定按钮。失败输出均保留，未放宽超时或绕过可见性。
- 本次仓库只修改前端实现、测试与文档；API 为已有实现部署，无新 Python 逻辑。未统计前端覆盖率，不宣称覆盖率达标；此前 Python 功能组未达门槛的证据仍见 ../batch-rerun-20261119/README.md。

## 发布与恢复
本机 API 使用 mini-eval-api:batch-config-20261119，基于 mini-eval-api:ui-layout-20261119；重建前确认无 STARTING/RUNNING/CANCEL_REQUESTED 执行，Compose API 环境与原容器一致。使用既有 compose/lan-compose 和 .runtime 部署覆盖文件，仅重建 API，不运行 migrate，不修改数据库卷。
原镜像与覆盖文件备份保留，可恢复覆盖文件后以相同 Compose 配置 --no-deps --no-build 重建 API。其他平台没有自动部署；须更新其 API 和前端，单独替换 Worker 无效。正式源码部署仍按迁移文档核对全部版本差异，不能将本次无迁移套用于跨版本升级。
