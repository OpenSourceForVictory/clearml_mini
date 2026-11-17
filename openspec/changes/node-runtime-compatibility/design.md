Root 宿主机继续使用 systemd system units、普通业务账号和设备隔离，实际读取 cgroup 限制而非单纯判断版本；非 root user manager 继续要求 v2 委派。v1/混合解析 /proc/self/cgroup 与 mountinfo，处理 cpu/cpuacct 合并挂载及不同控制器路径。验证必须包含 CPU、内存、PID，缺失或未限制拒绝。
Docker 首次只读检查只报告镜像缺失为待准备。确认后上传校验过的 bundle，优先使用现有镜像，否则拉取；仅默认 mini-eval-runner:dev 允许在目标节点从可信 runner Dockerfile 原生构建，不把本机架构镜像硬传远端，自定义镜像失败拒绝。准备后运行一次受限无网络容器核验资源限制，NPU 逐设备验证可打开分配卡及公共设备。并不宣称业务 CANN 可用，业务模板仍需单独探测。
完成镜像与限制验证后才 begin 注册/轮换凭据。预检和安装前重检仍保护原节点身份。阶段可见，执行时间有界，失败不泄露 SSH 凭据。保留失败与实测边界，已知 NPU 仅短暂探针不安装或覆盖节点。
