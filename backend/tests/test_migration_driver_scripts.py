"""Destructive migration drivers, opt-in against a disposable PostgreSQL server only."""
import os
from pathlib import Path
import runpy
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not os.getenv('MIGRATION_DRIVER_TEST_URL'),reason='requires dedicated disposable PostgreSQL server')
@pytest.mark.parametrize('script',['check_suite_migration.py','check_membership_migration.py','check_image_runtime_migration.py'])
def test_real_migration_driver_creates_isolated_database(monkeypatch,script):
    url=make_url(os.environ['MIGRATION_DRIVER_TEST_URL'])
    assert url.get_backend_name()=='postgresql'
    database='migration_driver_'+uuid.uuid4().hex
    admin=create_engine(url,isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as connection:connection.execute(text('CREATE DATABASE '+database))
        target=url.set(database=database).render_as_string(hide_password=False)
        monkeypatch.setenv('MIGRATION_TEST_DATABASE_URL',target)
        monkeypatch.chdir(ROOT)
        scope=runpy.run_path(str(ROOT/'scripts'/script),run_name='__main__')
        try:
            with scope['engine'].connect() as connection:
                assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar()
        finally:scope['engine'].dispose()
    finally:
        with admin.connect() as connection:connection.execute(text('DROP DATABASE IF EXISTS '+database+' WITH (FORCE)'))
        admin.dispose()
