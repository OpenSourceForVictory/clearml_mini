# 机器接入与资源池成员管理

管理设置分为机器管理、资源池管理、账号管理和客户端模板。机器凭据代表宿主机；一个 Worker 实例代表一张 GPU。同机各卡可加入不同池，CPU、内存、并发额度仍按机器合计。

## 新机器加入

1. 管理员在“机器管理”创建机器凭据，设置整机可分配额度。
2. 下载配置前确认 **Worker 接入 API 地址**：浏览器通过隧道访问的 localhost 通常不是新机器可以访问的平台地址。已有 Worker 本地隧道时可以填写对应回环地址。
3. 默认选择真实 GPU、“暂不分配资源池”。没有资源池也能接入。模拟 Worker 需显式选择模拟设备。
4. 把配置安全传到机器上（文件权限 600），安装 Docker / NVIDIA 容器运行环境并预置所需镜像，在项目目录执行：

~~~bash
python3 scripts/install_worker.py --config /secure/worker.json --instance evaluation --gpus all --check
python3 scripts/install_worker.py --config /secure/worker.json --instance evaluation --gpus all --start
~~~

也可 --gpus 0,2；每卡生成 evaluation-gpu0 等实例、独立工作目录和恢复路径。原来的 --config ... --instance gpu0 单卡用法保持兼容。

--check 只查询身份与服务能力、Docker、镜像和 GPU，不注册设备、不写配置、不改成员。认证失败时先检查 API 地址和机器凭据。首次安装后服务启动才会上报设备，页面由“等待连接”变为“已连接”。

重复安装默认拒绝覆盖。显式 --update 保留旧 state_path 和设备身份；更新共享程序前必须让所有已安装实例完成任务并停止服务，恢复日志中不得还有执行。不能删除恢复记录来绕过检查。安装器不会自动下载镜像、安装驱动或扫描热插拔。

## 加入、移出与换池

在“资源池管理”筛选全部、未分配或指定池，按机器全选**当前显示的已登记 GPU**或逐卡选择。预览列出来源、目标和持有执行数。目标选择空表示移出池；机器凭据、存储目录和历史任务保留。

同一 GPU 只有一个实际池。物理池允许混合型号；模拟设备不能加入物理池，显存低于目标池门槛也会拒绝。批量请求先全部校验，任一卡不兼容或版本过期则整体拒绝。轮询保留选择的版本，出现冲突后重新核对并刷新选择版本。

保存后立即退回未领取的预留任务至原池队列。已领取执行继续，任务和清理全部结束后该卡才能进入新池。一批中空闲卡可以先完成，其他卡显示“排空中”。目标池失效显示“目标不可用”，管理员可以重新提交目标；离线且有遗留执行的卡不能强制移走。

普通用户没有成员修改权限。资源摘要按实际归属统计，待加入数单列。基础就绪仅表示连接、成员、存储、磁盘及清理门槛满足；具体任务仍受 GPU 型号、显存、镜像和各级额度限制。

本版本支持创建池和修改 GPU 共享策略；尚不提供名称等全部基本信息修改、资源池删除或归档。移除成员不等于删除资源池。

## 协议与管理员 API

POST /api/v1/worker/register 的 pool_id 可省略或为 null；新无池设备自动进入平台管理模式。v4 Worker 不需要新协议版本。GET /api/v1/worker/self 用 Worker Bearer 凭据返回机器身份和 capabilities，不返回凭据，不产生注册副作用。

旧设备迁移后 revision=0，保留原本地注册方式；管理员管理一次后版本递增，永久以平台归属为准。此后重启传入旧甚至已失效 pool_id 也不会恢复原池。UUID、机器身份、GPU 编号和真实/模拟类型仍严格校验。

管理员 Bearer 凭据调用：

~~~http
PUT /api/v1/admin/resource-memberships
Content-Type: application/json

{"pool_id":"target-pool-id","members":[
  {"slot_id":"slot-uuid","expected_revision":1}
]}
~~~

最多 256 卡，移出时 pool_id=null。返回每卡实际/期望池、版本、成员状态及基础准入原因。HTTP 409 的 detail 包含逐卡原因；重复 slot_id 返回 400。变更审计保存在 membership_audits，包括操作者、旧实际池、目标池、版本与时间。

服务端保存、调度、claim、cleanup 共用 PostgreSQL 事务锁。成员版本未生效禁止新准入，续租和旧执行恢复继续有效。成员与存储版本相互独立，必须同时满足才可调度。

## 升级与回退

先备份数据库、API 镜像和宿主代码；先升级 API/数据库，再升级 Worker。Alembic d407e09 让 Slot.pool_id 可空，为旧卡回填 desired_pool_id=pool_id，两版本均为 0，原实际池不变。

回退到 c396d08 前必须完成排空并把所有未分配卡加入兼容池；降级脚本检测到空归属或未完成迁移会拒绝。不得为降级删除有历史执行的设备。恢复旧 Worker 前也要同步其本地配置，避免旧软件按旧池重新注册。保留数据库备份、恢复日志和目录快照。

迁移检查仅对专用空库执行：

~~~bash
MIGRATION_TEST_DATABASE_URL=postgresql+psycopg://... python3 -m scripts.check_membership_migration
~~~
