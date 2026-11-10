"""Plan fictional records, or explicitly apply them to a reviewed dedicated database."""

import argparse
import getpass
import json
from pathlib import Path

from sqlalchemy.engine import make_url

from backend.app.core.config import Settings
from backend.app.db.runtime import Database
from backend.app.db.simulation import seed_simulation

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--ack-database", choices=("smartticket_ai", "smartticket_ai_test"))
    args = parser.parse_args()
    if not args.apply:
        print("只读计划：4个模拟账户、2商品/订单/支付/物流、1退款、1综合技能组；未连接数据库。")
    else:
        settings = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env")
        if (
            not settings.database_url
            or make_url(settings.database_url.get_secret_value()).database != args.ack_database
        ):
            raise ValueError("需明确配置和确认专用目标库")
        password = getpass.getpass("模拟账户密码（至少12字符，不回显）：")
        database = Database(settings)
        with database.sessions() as session:
            summary = seed_simulation(session, password)
            session.commit()
        database.dispose()
        print(json.dumps(summary, ensure_ascii=False))
