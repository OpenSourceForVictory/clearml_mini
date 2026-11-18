# WS 传输连通性修复（2026-11-18）

现场错误证据：client_service_unreachable，reason=unexpected_error，exception=ClientResponseError，status=400，补充信息 unsupported http method: HEAD。旧实现把 WS 根路径转成 HTTP HEAD，非根路径要求业务握手；两者都超出了传输可达性检查的职责。

修复前回归：test_ws_transport_connectivity.py 的空路径、根路径、/requires-auth 共 3 failed。前两项实际发出 HEAD，违反“不发送应用请求字节”的断言；非根路径握手收到本地测试服务的 400，报 WSServerHandshakeError。此处是本地模拟拒绝服务，未声称复现现场完整网络或解析器行为。

修复：WS 全路径 TCP，WSS TCP/TLS 默认校验证书；校验全部 DNS 结果并连接固定 IP；共用 3 秒截止时间，关闭连接，不发送 HTTP 或业务握手。HTTP(S) HEAD 不变。日志 TCP/TLS 的 status 为 -，HTTP 响应解析异常归类 http_response；不记录敏感 URL 或异常原文。

验证命令：
```bash
PYTEST_ADDOPTS=--import-mode=importlib .venv/bin/python -m pytest backend/tests/test_ws_transport_connectivity.py backend/tests/test_client_connectivity_diagnostics.py backend/tests/test_readiness_probes.py -q
```
结果：38 passed，无跳过。覆盖本地 TCP/TLS 连接、不可信证书、系统信任证书、根/非根路径无请求字节、代理忽略、DNS 安全、多地址回退及无二次 DNS、空解析、拒绝连接、超时/取消、既有 HTTP 与服务端就绪检测。部分失败路径使用注入，不代表现场验收。TypeScript、OpenSpec strict、git diff --check 通过。

未执行全量、统一目录行/分支覆盖率统计或真机业务，不宣称达到覆盖率门槛。未部署。客户端节点需更新 worker/client_connectivity.py 并在安全停机后重启对应 Worker，平台新建执行尝试；不需要 API 修改或数据库迁移。诊断仅证明传输可达，WS 路径/鉴权/子协议与业务就绪另行验证。
