# 纳管、解除纳管与重新纳管验收

2026-11-17，在实体节点 rtx3080-182（192.168.8.182，RTX 3080）完成平台生命周期闭环。使用真实 API、SSH、systemd Agent/Worker/updater 和 Docker 执行；业务为 mock 评测，不代表真实模型推理或模型效果验证。

## 自动化用例

重新运行 test_administration.py、test_enrollment.py、test_ssh_onboarding.py、test_node_installation.py：23 项通过。覆盖执行期间解除拦截、停用后完成任务、解除后的凭据拒绝、历史保留、重新纳管身份保持、所有设备和组件版本就绪门槛、配置版本冲突、失败回滚和传输清理。

Playwright node-enrollment.spec.ts 和 ssh-onboarding.spec.ts：2 项通过。首次尝试因本地 Vite 未启动而连接被拒绝；启动开发服务后复跑通过。浏览器测试使用 API 替身，不能代替下面的真机验收。

## 实体节点闭环

1. 确认节点在线、无占用执行；GPU 85 MiB 占用、利用率 0%。保存原资源池和节点配置，建立专用验收池。
2. 下发 10 回合 mock 任务；执行中请求解除纳管，API 返回 409，节点停止新任务准入，原任务继续完成。
3. 确认任务成功、占用清零；再次解除纳管成功，旧 Worker 凭据访问 worker/self 返回 401，历史任务仍可查。
4. 对原节点发起重新纳管，经 SSH 安装完整发布包；等待原设备、Agent 和升级执行器全部就绪后完成纳管。
5. 核对节点/设备 ID 不变，本地配置除连接凭据外保持一致；再次下发 10 回合任务，执行成功并释放资源。
6. 恢复原资源池，归档临时验收池，确认节点在线、启用且 held_executions=0。

[机器、任务 ID 与检查结果](rtx3080-182.json)。测试凭据和原配置仅保留于未跟踪的私有运行目录，不进入证据文件或 Git。

## 范围

本次验证的是现有 GPU 节点的真实解除与重新纳管，保留其原 huawei Worker 身份，没有迁移为新的 mini-eval 业务账号。全新 root 安装在独立 systemd 容器的验证见 [root 管理验收](../root-managed-host/README.md)。尚不能宣称全新实体机器 root 纳管或实体 NPU 完整生命周期也已通过。
