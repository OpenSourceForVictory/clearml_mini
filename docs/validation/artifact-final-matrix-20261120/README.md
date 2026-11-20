# 产物最终真机矩阵

验收平台：http://192.168.8.151:18000。下列批次及日志/文件保留在本机平台，需登录相应权限账号查看；不是独立临时平台。真机产生的样例产物用于功能验收，不是 RoboTwin 或模型质量评测。

| 场景 | 批次或证据 | 实际断言 |
| --- | --- | --- |
| 宿主原生 SDK、真实 CUDA、三回合 | 2851aba9-bb5e-43a4-b30a-de703d348528；[SDK发布](../artifact-manifest-release-20261120/README.md) | 安装包SDK、普通dgh账号、CUDA算术、三回合目录/seed/manifest归属；JSON预览 |
| 宿主直接协议、逐回合三次 | fb32581d-ce7b-4bb0-beac-96f2b8eb106a；[运行验证](../artifact-manifest-release-20261120/README.md) | 每回合argv/env/input路径一致、JSON与WebM预览拖动 |
| Docker普通命令 | ee71d7b5-8596-457a-94e9-3e3b66268bb9 | 持久挂载、容器清理后API读取及浏览器视频seek |
| Docker原生SDK三回合 | e85f6e53-c9f8-432b-8aa8-f6f727aafc7f；本目录docker-sdk文件 | 安装包SDK、100000起seed、三个不同目录及归属；清理后JSON/视频2秒和seek1秒 |
| 宿主/Docker图片 | 3078b497-66da-46e8-92bf-b3f6654254f6 / fc7c5738-fb9a-4998-a9a2-795ad6b019b1 | 真实1×1PNG预览、浏览器下载字节与节点文件完全一致 |
| 宿主/Docker自定义浏览目录 | [目录验收](../custom-result-directory-20261120/README.md) | 首次领取快照、参数解析、实时目录、页面切换；宿主UID1001与Docker UID0 |
| 64MiB文件 | 3d7507fc-3e6a-4f13-8eb6-0a110ab50360 | 清理后HEAD长度、首尾Range；不是吞吐基准 |
| Worker实际停机 | [offline-evidence](../custom-result-directory-20261120/offline-evidence.json) | Agent下发停止、systemd inactive，API仍可预览JSON/WebM及seek；随后恢复原配置。Agent及共享存储保持运行 |
| NPU服务双实例 | [NPU验收](../npu-final-validation-20261120/README.md) | 同节点不同卡同名服务，真实torch_npu算术、503至200就绪、设备与日志隔离、停止清理；用户确认NPU无产物，不要求NFS |

API只读挂载的节点根与API根不同，沿用artifact-validation身份映射。Worker 0.21.12已通过界面升级本机GPU节点，其他节点未批量升级。最终前端布局发布不改变Worker或数据库c952f34，发布记录见layout-deployment.json。不同阶段证据保持各自版本，不合并为一次模型测试。NPU界面部署与日志另见[服务管理](../service-placement-followup-20261120/README.md)。
