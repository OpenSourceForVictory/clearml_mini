# 评测向导资源配置布局修复（2026-11-20）

用户报告资源配置页出现“修改服务端资源”长条。托管服务摘要使用 aside，命中全局导航样式 position:fixed、top:0、bottom:0，桌面形成贯穿屏幕的遮挡条。修复前浏览器断言实际 position=fixed 失败，见 [证据](sidebar-failure.txt)、[截图](sidebar-before.png)。导航样式现限定 .shell > aside；资源摘要使用普通 section 卡片，见 [桌面](sidebar-after.png)、[窄屏](mobile-after.png)。

同时修复资源要求及高级约束只占半列（1051px 容器内仅514.5px）、字段来源拥挤，以及第四步 sticky 操作栏遮挡末字段/恢复按钮。完整资源组跨行，内部桌面双列、窄屏单列，第四步操作栏回到文档流。保留 [半列失败](before.txt)、[原图](before.png)、[遮挡失败](occlusion-failure.txt)。

最终冻结源码后浏览器关联测试 **50 通过、0 失败、0 跳过**，包括新增12项：1440/1024/390 × GPU/NPU/旧契约、三个屏宽托管服务摘要。断言非固定定位、边界/无溢出、末字段/恢复按钮不被遮挡、锁定字段、草稿与预检CPU值、服务实例数及跟随/独立资源池。保留 [测试输出](tests.txt)。中途修改代码引发热更新时出现DOM脱离、元素消失和超时，保留 intermediate.txt、intermediate-regression.txt，最终固定源码复测通过。

TypeScript 检查、Vite 构建通过；保留已有500kB打包提示，见 [构建](build.txt)。OpenSpec严格验证通过。界面指南、资源要求操作文档、Skill参考已同步。

本次只改前端，不涉及数据库迁移或Worker/Runner升级；未部署。浏览器使用模拟API，不创建真实任务、不作真机业务验收。前端未插桩，不将50项通过数称为代码覆盖率；本轮不重跑Python全量测试。

## 已发布

按用户要求发布到本机平台18000端口。镜像 mini-eval-api:wizard-resources-20261120，以现有 log-policy-02111 镜像为基础仅替换前端；保留原SDK下载包/API/Worker，未发布此前0.21.12产物manifest后端修改。保留所有既有Compose覆盖文件及共享挂载，仅追加界面镜像覆盖。部署前六节点均无活动执行，短暂暂停接纳，容器健康后恢复六节点enabled=true；数据库仍b841e23，无迁移。

真实平台草稿核查：使用既有集合与服务模板，设置两个实例，在1440/1024/390屏宽核验摘要为static、无横向溢出、返回服务步骤保留实例数；无请求数据模拟、未提交任务。见 [部署结果](deployment.json)、[浏览器输出](browser.log)、[线上桌面截图](deployed-1440.png)、[线上窄屏截图](deployed-390.png)。修复前镜像 mini-eval-api:log-policy-02111-20261120 保留，可使用原Compose覆盖文件恢复。
