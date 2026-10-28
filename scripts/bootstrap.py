"""Development/bootstrap entry point; requires DATABASE_URL and ADMIN_PASSWORD."""
import os
from argon2 import PasswordHasher
from sqlalchemy import select
from backend.app.core import Base, Pool, User, session_factory


def main():
    username = os.environ['ADMIN_USERNAME']
    password = os.environ['ADMIN_PASSWORD']
    if len(password) < 12:
        raise ValueError('ADMIN_PASSWORD must be at least 12 characters')
    factory = session_factory()
    Base.metadata.create_all(factory.kw['bind'])
    with factory() as s:
        if not s.scalar(select(User).where(User.username == username)):
            s.add(User(username=username, password_hash=PasswordHasher().hash(password), role='admin'))
        if not s.scalar(select(Pool).where(Pool.name == 'mock')):
            s.add(Pool(name='mock', gpu_model='virtual', min_vram_gb=0))
        s.commit()


if __name__ == '__main__':
    main()
