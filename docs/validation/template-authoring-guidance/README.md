# 运行模板注册与查看验证（2026-11-16）

本次完成两层页签、分类型草稿保留、表单式只读查看、上下文说明、固定命令逐项输入和参数来源映射、统一目录挂载，以及六种完整样例的预览/下载/载入草稿。API、调度和 Worker 契约未修改。

验证结果：

- TypeScript 检查与 Vite 构建通过。
- Playwright：模板注册、只读查看、草稿/复制、参数映射冲突、统一挂载、样例载入、提交向导和机器存储页面，共 16 项通过（17.5 秒）。其中键盘 Enter 激活注册页签另行验证 1 项通过。
- 示例测试 4 项通过：六份模板通过 API schema；下载与源码一致；SDK/direct 客户端真实 HTTP 回显及逐回合结果；隔离 Docker network=none 中相同程序验证通过（1.21 秒）。Docker 基础镜像使用本机 mini-eval-runner:dev，制品为 mini-eval-template-examples:v1。
- 相关后端 task_input、parameter_contract、directory_mounts、typed_template_flow、generic_templates：67 通过、2 项环境依赖跳过、1 弃用警告（20.20 秒）。
- 桌面只读配置和 390px 窄屏注册页截图已人工查看。发现说明区受全局 aside 固定定位样式影响、只读字段受全局 dl 网格影响，已限定组件结构和样式并回归通过。
- 未将此次功能验证记为真实模型评测，也未声称六份模板已部署至所有物理节点。

生产已于 2026-11-16 10:07（北京时间）发布前端 26be4a0。用户明确要求现在上线，因此采用静态文件原子更新：先写入带指纹的资源与样例，最后替换 index.html，保留旧资源。API 容器 ID 和启动时间保持不变，未重启后端、数据库或 Worker；后台两小时测试继续运行。

本机 127.0.0.1:18000 和内网 192.168.8.151:18000 的健康检查、9 个静态文件 SHA-256 均通过。真实账号浏览器验证了页签、只读查看、样例载入、参数映射、目录选择、草稿保留和服务端说明，没有注册测试模板或提交任务。五台 Worker/Agent 版本、制品摘要、在线及配置回执检查通过。

持久化制品 mini-eval-api:template-ui-26be4a0 的摘要为 sha256:173d16290c9362fccaad7418a49fdca46ff41ce51183626599ff9d74fcd42399，基于当前后端镜像，仅覆盖 frontend/dist。现运行容器仍显示原后端镜像名，静态目录为新版；.runtime/fleet-release/image.yml 的 api 指向新制品摘要，后续按同一 Compose overlay 重建仍保留新版。migrate/bootstrap 镜像不变。

发布证据、旧页面、旧 overlay 在 .runtime/template-ui-release/。如需回退界面，可将 previous-dist/index.html 暂存后原子替换回运行容器的 /workspace/frontend/dist/index.html（旧带指纹资源仍在），并恢复 previous-image.yml；不回滚数据库。

后续资源要求（CPU、内存、GPU/NPU、共享/独占）需要贯通模板契约、所有提交入口、预检、调度及执行能力；本次界面提交不代表该功能已实现。
