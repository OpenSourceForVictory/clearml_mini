# 初次失败证据

## endpoint_migration.log

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_endpoint_backfill_constraint_and_downgrade_guard _____________
backend/tests/integration/test_endpoint_migration.py:32: in test_endpoint_backfill_constraint_and_downgrade_guard
    with factory() as s:assert s.execute(text('SELECT version_num FROM alembic_version')).scalar()==current_revision
E   AssertionError: assert 'ab852c3' == 'ad074e5'
E     
E     - ad074e5
E     + ab852c3
----------------------------- Captured stdout call -----------------------------
No new upgrade operations detected.
- generated xml file: /home/dgh/Code/clearml_mini/.runtime/skipped-specialists-20261124/endpoint_migration.xml -
=========================== short test summary info ============================
FAILED backend/tests/integration/test_endpoint_migration.py::test_endpoint_backfill_constraint_and_downgrade_guard
1 failed in 3.21s

```

## orchestration_migration.log

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________________ test_template_migration_upgrade_and_downgrade _________________
backend/tests/integration/test_orchestration_migration.py:12: in test_template_migration_upgrade_and_downgrade
    migrate('upgrade','n407c89');engine=create_engine(url)
backend/tests/integration/test_orchestration_migration.py:11: in migrate
    def migrate(*args):subprocess.run(['.venv/bin/alembic','-c','backend/alembic.ini',*args],env=env,check=True,capture_output=True,text=True)
/opt/anaconda3/lib/python3.13/subprocess.py:577: in run
    raise CalledProcessError(retcode, process.args,
E   subprocess.CalledProcessError: Command '['.venv/bin/alembic', '-c', 'backend/alembic.ini', 'upgrade', 'n407c89']' returned non-zero exit status 1.
- generated xml file: /home/dgh/Code/clearml_mini/.runtime/skipped-specialists-20261124/orchestration_migration.xml -
=========================== short test summary info ============================
FAILED backend/tests/integration/test_orchestration_migration.py::test_template_migration_upgrade_and_downgrade
1 failed in 0.23s

```

## resource_migration.log

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____________ test_resource_contract_migration_and_downgrade_guard _____________
backend/tests/integration/test_resource_migration.py:13: in test_resource_contract_migration_and_downgrade_guard
    migrate('upgrade','r841a23');engine=create_engine(url)
backend/tests/integration/test_resource_migration.py:12: in migrate
    return subprocess.run(['.venv/bin/alembic','-c','backend/alembic.ini',*args],env=env,check=True,capture_output=True,text=True)
/opt/anaconda3/lib/python3.13/subprocess.py:577: in run
    raise CalledProcessError(retcode, process.args,
E   subprocess.CalledProcessError: Command '['.venv/bin/alembic', '-c', 'backend/alembic.ini', 'upgrade', 'r841a23']' returned non-zero exit status 1.
- generated xml file: /home/dgh/Code/clearml_mini/.runtime/skipped-specialists-20261124/resource_migration.xml -
=========================== short test summary info ============================
FAILED backend/tests/integration/test_resource_migration.py::test_resource_contract_migration_and_downgrade_guard
1 failed in 0.23s

```

## retention_migration.log

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____________ test_retention_upgrade_defaults_and_downgrade_guard ______________
backend/tests/integration/test_retention_migration.py:29: in test_retention_upgrade_defaults_and_downgrade_guard
    migrate('check');migrate('downgrade','g730b12');migrate('upgrade','head')
backend/tests/integration/test_retention_migration.py:11: in migrate
    assert (result.returncode==0)==ok,result.stdout+result.stderr
E   AssertionError: FAILED: Target database is not up to date.
E     
E   assert (255 == 0) == True
E    +  where 255 = CompletedProcess(args=['/home/dgh/Code/clearml_mini/.venv/bin/python', '-m', 'alembic', '-c', 'backend/alembic.ini', 'check'], returncode=255, stdout='FAILED: Target database is not up to date.\n', stderr='').returncode
- generated xml file: /home/dgh/Code/clearml_mini/.runtime/skipped-specialists-20261124/retention_migration.xml -
=========================== short test summary info ============================
FAILED backend/tests/integration/test_retention_migration.py::test_retention_upgrade_defaults_and_downgrade_guard
1 failed in 5.33s

```

## Re-upgrade exposes missing index

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________ test_endpoint_backfill_constraint_and_downgrade_guard _____________
backend/tests/integration/test_endpoint_migration.py:37: in test_endpoint_backfill_constraint_and_downgrade_guard
    command.upgrade(cfg,'head');command.check(cfg)
.venv/lib/python3.13/site-packages/alembic/command.py:300: in check
    raise util.AutogenerateDiffsDetected(
E   alembic.util.exc.AutogenerateDiffsDetected: New upgrade operations detected: [('add_index', Index('ix_job_events_run_status_created', Column('run_id', String(length=36), table=<job_events>), Column('new_status', String(length=30), table=<job_events>, nullable=False), Column('created_at', DateTime(timezone=True), table=<job_events>, nullable=False, default=CallableColumnDefault(<function now at 0x7fb2a67f25c0>))))]
----------------------------- Captured stdout call -----------------------------
No new upgrade operations detected.
- generated xml file: /home/dgh/Code/clearml_mini/.runtime/skipped-specialists-20261124/endpoint_migration.xml -
=========================== short test summary info ============================
FAILED backend/tests/integration/test_endpoint_migration.py::test_endpoint_backfill_constraint_and_downgrade_guard
1 failed in 3.97s

```
