# 前端布局与动态映射发布（2026-11-19）

本机 18000 原静态资源 index-OrYuPrvx.js / index-BX3MhEKG.css，仍含旧 nowrap 统计列且没有 result-outcomes。此次发布最新静态构建 index-k65ANIta.js / index-BPYPX-Li.css（源代码 56210eb），先复制带哈希 assets，再原子替换 index.html，保留旧资源供已打开页面使用。API 健康 200，发布前活跃 Run 为 0；未重启 API 或节点进程，未迁移数据库。原 index 在本地 .runtime/ui-layout-20261119 保留，不提交该目录。

已构建 mini-eval-api:ui-layout-20261119（原部署 API 镜像加静态资源），更新本部署使用的 image.yml 中 API 镜像引用；运行容器仍标识原镜像，容器可写层静态资源已更新，后续按现有 Compose 配置重建会使用新镜像。不覆盖其他机器部署。

开发服务器 21 项通过；已部署静态页面 20 项通过，API 使用测试路由模拟，不代表生产数据或模型验收。首次部署页回归 20 通过/1 失败，是某用例直接动态 import /src/api.ts，仅适用于 Vite 开发服务；正式构建不提供该源文件。该用例在开发服务已通过；生产资源回归明确排除这一开发专用用例，失败证据保留。

实际 HTTP 校验确认 JS 含动态映射未记录提示和 result-outcomes，CSS 含修复布局。此次不含尚未实施的实际启动快照，也不包含本地尚未部署的 API/Worker 功能。
