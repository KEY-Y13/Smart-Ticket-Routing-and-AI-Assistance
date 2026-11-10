"""Explicit first-admin creation after schema review/application; dry-run by default."""

import argparse
import getpass
from pathlib import Path

from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.app.db.bootstrap import initialize_policies
from backend.app.db.models import User
from backend.app.db.runtime import Database
from backend.app.schemas.business import UserCreate

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", default="admin")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ack-database", choices=("smartticket_ai", "smartticket_ai_test"))
    args = parser.parse_args()
    if not args.apply:
        print("只读计划：迁移后创建首个管理员与 SLA/派单锁初始配置；未连接数据库。")
    else:
        settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
        if not settings.database_url:
            raise RuntimeError("未配置专用数据库连接")
        from sqlalchemy.engine import make_url

        if make_url(settings.database_url.get_secret_value()).database != args.ack_database:
            raise RuntimeError("必须用 --ack-database 明确目标专用库")
        password = getpass.getpass("新管理员密码（至少12字符，不回显）：")
        confirmation = getpass.getpass("再次输入：")
        if password != confirmation:
            raise ValueError("两次密码不一致")
        body = UserCreate(login=args.login, password=password, role="ADMIN")
        database = Database(settings)
        with database.sessions() as session:
            if session.scalar(select(User.id).where(User.role == "ADMIN")) is not None:
                raise RuntimeError("管理员已存在，拒绝覆盖或重置")
            session.add(
                User(
                    login=body.login,
                    role=body.role,
                    password_hash=hash_password(body.password.get_secret_value()),
                )
            )
            initialize_policies(session)
            session.commit()
        database.dispose()
        print("首个管理员及基础配置已建立；未输出密码或哈希。")
