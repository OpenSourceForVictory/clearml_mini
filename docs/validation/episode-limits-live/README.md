# 2026-11-18 可选执行限制与逐回合真机验收

环境为本机实验平台、RTX 4090 和 29 号 Ascend 910B3，不代表用户不可访问的另一套部署。

## 失败证据与修复

- 首轮 GPU continue/stop 客户端：任务 bba77793-f390-4888-bb7d-ac5eef285c21、61b44b43-e402-42a2-89fb-187384fee00e 均失败。Worker journal 在 verify_group 读取 cpu.max 时 FileNotFoundError，恢复显示 previously created host unit is missing。不限轴不一定启用 cgroup 控制器；0.20.1 改为仅校验有限轴，不降低有限资源或 pids 校验。
- 首轮 NPU 客户端等待：节点仅有 server 角色；临时增加 client 角色用于验证，结束恢复。
- 首轮 NPU 启动脚本权限不足：上传的验收脚本为 root 0600；仅将该验收脚本设为 0644 后重试，未更改业务目录权限。
- 回归：test_resource_limits.py 与 test_host_backend_lifecycle.py 合计 68 passed，覆盖 v1/v2 无控制器文件、有限轴缺文件失败。未执行完整覆盖率统计，不宣称满足全仓覆盖率门槛。

## 发布准备

数据库备份及独立恢复、迁移演练通过；实际数据库升级至 x407a89。仅升级两个验收节点的 Agent/Worker 包，其余节点保持版本。真实业务验收结果待下面补充。

## 实测结果

部署镜像：mini-eval-api:episode-limits-fix-20261118（含前端），mini-eval-onboarding:episode-limits-20261118。两个节点的 Agent/Worker bundle 已更新，Worker 为 0.20.1 / 协议 17。容器验收使用 mini-eval-runner:episode-limits-live，保留现有业务镜像；未替换所有已纳管节点或所有业务容器的 Runner。

| 用例 | 结果 |
| --- | --- |
| GPU 普通命令逐回合 continue | 真实 CUDA 张量计算 3 次；退出码 1、0、0，整体 FAILED 符合预期 |
| NPU 普通命令逐回合 continue | 真实 torch_npu 张量计算 3 次；退出码 1、0、0，整体 FAILED 符合预期 |
| GPU/NPU 默认 stop | 各完成一次真实设备计算，首回合退出 1，后两回合未启动 |
| 宿主机平台协议 direct / SDK command | 两个批次各 3 回合完成，均 SUCCEEDED；中文、空格、带中划线参数 task-config 经集合参数绑定 argv 成功 |
| 每批两个 NPU 服务实例 | 共 4 次真实 NPU 服务启动；延迟就绪先返回 503，客户端 RESERVED，服务 HEALTHY 后才实际启动客户端；每回合再 GET 断言 ready=true |
| Docker 普通命令，有限 / 不限 | 两项 SUCCEEDED；验证参数、模板环境变量和设备请求。容器内未安装 torch，此项不是容器 GPU/NPU 算子验收 |
| Docker 取消，有限 / 不限 | 两项 CANCELLED 且 cleanup_confirmed；有限为 NanoCpus=1000000000 / Memory=2147483648，不限为 0 / 0，GPU DeviceRequests 保留 |
| 工作目录外写入 | GPU 的 dgh、NPU 的 HwHiAiUser 均能在 /tmp 创建并删除自己的测试目录；不代表对任意目录拥有写权限 |

客户端 systemd 读取到 CPUQuotaPerSecUSec=infinity、MemoryMax=infinity、RuntimeMaxUSec=infinity；NPU 服务 CPU/内存同为 infinity，服务原有长期运行时限保持不变。数据库已领取执行仍预约 1 CPU / 2 GiB，未按运行限额 0 计账。

普通命令 continue 预期整体失败，不能用整体 FAILED 判定验收不通过；具体退出码及任务/批次 ID 见 [机器可读结果](results.json)。首次失败仍保留在平台执行历史，未删改为成功。测试模板已归档、历史保留；临时 NPU 客户端角色、节点启用状态、临时镜像审批已恢复，远端测试脚本已删除，持有执行数为 0。

相关回归额外 44 passed（Docker contract + host shell episode），合计本次两个测试组 112 passed；保留一条 Starlette 弃用警告。OpenSpec 严格校验通过。没有运行全量测试、完整覆盖率统计或浏览器 UI 真机操作，接口返回的双实例与回合状态不等于浏览器交互验收。

[业务探针](business_probe.py)只做小张量计算、参数/环境检查、可写路径与延迟 HTTP 就绪检查。这里是真实硬件和调度链路验收，**不是 RoboTwin 或真实模型质量评测**。对登录 Shell 的验证限于这两个账号当前配置；没有注入冲突的 Conda/PYTHONHOME/动态库环境，不证明其他节点登录环境与显式环境配置兼容。用户报告的“显式环境成功而登录环境失败”仍待具体报错定位。

验收脚本曾因缺少 orchestration Idempotency-Key 返回 400，补请求头后通过；临时 Docker 构建首次因 .dockerignore 排除 .runtime 中探针失败，改用仅含 runner 与探针的独立构建上下文后成功。二者均为验收准备失败，不作为产品功能通过证据。完整私有现场记录与备份保留本地 .runtime/episode-limits-live-20261118，未入 Git。
