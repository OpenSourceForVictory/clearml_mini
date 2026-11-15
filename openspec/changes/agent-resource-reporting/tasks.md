# Tasks
## 1. Agent resource source
- [x] 1.1 实现版本化CPU/GPU快照、采样有效期及非阻塞事件确认，测试慢探测、慢落盘和时间字段，更新说明。
## 2. Worker control separation
- [x] 2.1 将Worker探测和配置应用移出心跳，验证慢NFS/命令不影响控制循环、过期缓存关闭准入、旧节点兼容。
## 3. Platform integration
- [x] 3.1 协商Agent资源来源、GPU身份校验和CPU/内存准入，测试多卡、乱序/过期、旧协议和租约独立性。
## 4. Verification
- [x] 4.1 运行相关及完整回归，记录迁移边界、验证结果并提交代码。

## 5. Heterogeneous inventory
- [x] 5.1 增加GPU/NPU统一设备清单与CPU-only Agent兼容，验证未知NPU指标不进入CUDA准入，记录NPU执行支持边界。
