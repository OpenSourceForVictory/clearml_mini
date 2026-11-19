# Python 跳过原因

```text
SKIPPED [1] backend/tests/integration/test_agent_mount_loss.py:8: disposable privileged mount namespace required
SKIPPED [1] backend/tests/integration/test_batch_controls_migration.py:6: separate disposable migration database required
SKIPPED [1] backend/tests/integration/test_bundled_onboarding.py:8: requires isolated mini-eval-onboarding-test Compose project
SKIPPED [1] backend/tests/integration/test_endpoint_migration.py:10: dedicated migration database required
SKIPPED [1] backend/tests/integration/test_endpoint_race.py:12: dedicated PostgreSQL required
SKIPPED [8] backend/tests/integration/test_orchestration_execution.py:55: real process/container execution opt-in
SKIPPED [1] backend/tests/integration/test_orchestration_migration.py:8: dedicated disposable orchestration migration database required
SKIPPED [1] backend/tests/integration/test_resource_migration.py:8: dedicated disposable resource migration database required
SKIPPED [1] backend/tests/integration/test_retention_migration.py:6: separate disposable migration database required
SKIPPED [1] backend/tests/integration/test_retention_mounts.py:7: explicit disposable mount-namespace test required
SKIPPED [1] backend/tests/integration/test_retention_race.py:12: dedicated PostgreSQL required
SKIPPED [4] backend/tests/integration/test_scheduling_sample.py:10: real host/docker sample opt-in
SKIPPED [4] backend/tests/integration/test_scheduling_sample.py:16: real host/docker sample opt-in
SKIPPED [2] backend/tests/test_directory_mounts.py:89: requires local Docker image
SKIPPED [1] backend/tests/test_execution_logs_postgres.py:53: requires disposable PostgreSQL server
SKIPPED [1] backend/tests/test_execution_logs_postgres.py:91: requires disposable PostgreSQL server
SKIPPED [1] backend/tests/test_execution_logs_postgres.py:116: requires disposable PostgreSQL server
SKIPPED [1] backend/tests/test_execution_logs_postgres.py:140: requires disposable PostgreSQL server
SKIPPED [24] backend/tests/test_generic_commands.py:96: opt-in real Docker matrix
SKIPPED [1] backend/tests/test_host_backend.py:33: set HOST_EXECUTION_TEST=1 for real systemd user units
SKIPPED [1] backend/tests/test_host_backend.py:61: real systemd opt-in
SKIPPED [1] backend/tests/test_host_backend.py:78: real systemd opt-in
SKIPPED [4] backend/tests/test_host_working_directory.py:96: requires systemd user manager
SKIPPED [1] backend/tests/test_managed_host.py:72: requires root, systemd and provisioned mini-eval test account
SKIPPED [4] backend/tests/test_managed_host.py:81: requires root, systemd and provisioned mini-eval test account
SKIPPED [16] backend/tests/test_service_readiness_execution.py:32: requires Docker and delegated systemd
SKIPPED [2] backend/tests/test_service_readiness_execution.py:75: requires Docker and delegated systemd
SKIPPED [2] backend/tests/test_service_readiness_execution.py:97: requires Docker and delegated systemd
SKIPPED [2] backend/tests/test_service_readiness_execution.py:117: requires Docker and delegated systemd
SKIPPED [4] backend/tests/test_service_recovery.py:47: requires local Docker image and real systemd opt-in
SKIPPED [4] backend/tests/test_service_recovery.py:67: requires local Docker image and real systemd opt-in
SKIPPED [2] backend/tests/test_service_recovery.py:87: requires local Docker image and real systemd opt-in
SKIPPED [2] backend/tests/test_service_recovery.py:108: requires local Docker image and real systemd opt-in
SKIPPED [1] backend/tests/test_template_examples.py:79: requires built mini-eval-template-examples:v1 image
SKIPPED [1] backend/tests/test_worker_storage.py:96: set STORAGE_PROBE_TEST_IMAGE for real Docker probe
```

前端 10 项跳过见 frontend.log；本轮未提供真实部署账号或 live API 开关。
