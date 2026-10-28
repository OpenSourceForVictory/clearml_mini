import os
import sys
from pathlib import Path
import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_admin_bootstrap_and_user_creation(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "management.db"}')
    monkeypatch.setenv('ADMIN_USERNAME', 'root-admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'long-admin-password')
    from backend.app.core import Base, User, session_factory
    from scripts.bootstrap import main as bootstrap
    from scripts.create_user import create_user
    factory = session_factory()
    Base.metadata.create_all(factory.kw['bind'])
    bootstrap()
    bootstrap()
    created_id = create_user('alice', 'long-user-password')
    with factory() as session:
        users = session.scalars(select(User).order_by(User.username)).all()
        assert len(users) == 2
        assert users[0].id == created_id
        assert users[0].role == 'user'
        assert PasswordHasher().verify(users[0].password_hash, 'long-user-password')
        assert users[1].role == 'admin'
    with pytest.raises(ValueError, match='already exists'):
        create_user('alice', 'long-user-password')
    with pytest.raises(ValueError, match='at least 12'):
        create_user('short', 'weak')
