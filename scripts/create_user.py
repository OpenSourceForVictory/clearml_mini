"""Create a local account from operator-provided environment variables."""
import os
from argon2 import PasswordHasher
from sqlalchemy import select
from backend.app.core import User, session_factory


def create_user(username: str, password: str, role: str = 'user') -> str:
    if not username or len(username) > 100:
        raise ValueError('USERNAME must be 1..100 characters')
    if len(password) < 12:
        raise ValueError('USER_PASSWORD must be at least 12 characters')
    if role not in ('admin', 'user'):
        raise ValueError('USER_ROLE must be admin or user')
    with session_factory()() as session:
        if session.scalar(select(User).where(User.username == username)):
            raise ValueError('username already exists')
        user = User(username=username, password_hash=PasswordHasher().hash(password), role=role)
        session.add(user)
        session.commit()
        return user.id


if __name__ == '__main__':
    print(create_user(os.environ['USERNAME'], os.environ['USER_PASSWORD'], os.getenv('USER_ROLE', 'user')))
