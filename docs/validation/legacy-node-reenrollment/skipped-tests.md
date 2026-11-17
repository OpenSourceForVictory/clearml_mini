# 跳过测试的原因

全量运行共 100 项跳过；不视作通过或真实环境验收。

| 数量 | pytest 原因 |
| ---: | --- |
| 1 | disposable privileged mount namespace required |
| 2 | separate disposable migration database required |
| 1 | requires isolated mini-eval-onboarding-test Compose project |
| 1 | dedicated migration database required |
| 2 | dedicated PostgreSQL required |
| 8 | real process/container execution opt-in |
| 1 | dedicated disposable orchestration migration database required |
| 1 | dedicated disposable resource migration database required |
| 1 | explicit disposable mount-namespace test required |
| 8 | real host/docker sample opt-in |
| 2 | requires local Docker image |
| 24 | opt-in real Docker matrix |
| 1 | set HOST_EXECUTION_TEST=1 for real systemd user units |
| 2 | real systemd opt-in |
| 4 | requires systemd user manager |
| 5 | requires root, systemd and provisioned mini-eval test account |
| 22 | requires Docker and delegated systemd |
| 12 | requires local Docker image and real systemd opt-in |
| 1 | requires built mini-eval-template-examples:v1 image |
| 1 | set STORAGE_PROBE_TEST_IMAGE for real Docker probe |
