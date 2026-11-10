"""Provision the explicitly authorized project-only MySQL instance; never touches MySQL80."""

import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import pymysql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from backend.app.core.security import hash_password
from backend.app.db.bootstrap import initialize_policies
from backend.app.db.models import User
from backend.app.db.simulation import seed_simulation

ROOT = Path(__file__).resolve().parents[1]
INSTANCE = ROOT / ".mysql-local"
MYSQL_HOME = Path(r"D:\JAVAsoftware\MySQL\MySQL Server 8.0")
PORT = 33307


def dsn(user, password, database):
    return URL.create(
        "mysql+pymysql",
        username=user,
        password=password,
        host="127.0.0.1",
        port=PORT,
        database=database,
    ).render_as_string(hide_password=False)


def own_server(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@datadir,@@port")
        datadir, port = cursor.fetchone()
    if Path(datadir).resolve() != (INSTANCE / "data").resolve() or int(port) != PORT:
        raise RuntimeError("拒绝对非项目实例执行任何写入")


def main():
    if INSTANCE.exists() or (ROOT / ".env").exists():
        raise RuntimeError("实例或配置已存在；拒绝覆盖。请用 start_local_mysql.py 恢复。")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    INSTANCE.mkdir()
    account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
    subprocess.run(
        ["icacls", str(INSTANCE), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
        check=True,
        capture_output=True,
    )
    credentials = {
        name: secrets.token_urlsafe(32)
        for name in (
            "root_password",
            "app_password",
            "test_password",
            "admin_password",
            "demo_password",
            "jwt_secret",
        )
    }
    private = INSTANCE / "credentials.json"
    private.write_text(json.dumps(credentials, indent=2), encoding="utf-8")
    data = INSTANCE / "data"
    error = INSTANCE / "mysql-error.log"
    base = [
        str(MYSQL_HOME / "bin/mysqld.exe"),
        "--no-defaults",
        f"--basedir={MYSQL_HOME}",
        f"--datadir={data}",
    ]
    with (INSTANCE / "initialize.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [*base, "--initialize-insecure", "--console"],
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if result.returncode:
        raise RuntimeError("初始化失败，详见项目私有 initialize.log；未删除任何数据")
    init = INSTANCE / "root-init.sql"
    init.write_text(
        "ALTER USER 'root'@'localhost' IDENTIFIED BY '" + credentials["root_password"] + "';\n",
        encoding="utf-8",
    )
    command = [
        *base,
        f"--port={PORT}",
        "--bind-address=127.0.0.1",
        "--mysqlx=0",
        f"--log-error={error}",
        f"--pid-file={INSTANCE / 'mysql.pid'}",
        "--default-time-zone=+00:00",
        "--character-set-server=utf8mb4",
        "--default-storage-engine=InnoDB",
        "--local-infile=0",
        f"--init-file={init}",
    ]
    with (INSTANCE / "process.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    (INSTANCE / "instance.json").write_text(
        json.dumps(
            {"port": PORT, "pid": process.pid, "mysql_home": str(MYSQL_HOME), "datadir": str(data)},
            indent=2,
        ),
        encoding="utf-8",
    )
    connection = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            connection = pymysql.connect(
                host="127.0.0.1",
                port=PORT,
                user="root",
                password=credentials["root_password"],
                autocommit=True,
                charset="utf8mb4",
            )
            break
        except pymysql.Error:
            time.sleep(0.5)
    if connection is None:
        raise RuntimeError("项目 MySQL 未就绪，保留私有日志与数据以供检查")
    own_server(connection)
    with connection.cursor() as cursor:
        for database in ("smartticket_ai", "smartticket_ai_test"):
            cursor.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    for database in ("smartticket_ai", "smartticket_ai_test"):
        env = {
            **os.environ,
            "SMARTTICKET_DATABASE_URL": dsn("root", credentials["root_password"], database),
            "SMARTTICKET_ALLOW_MIGRATION": database,
        }
        result = subprocess.run(
            [str(ROOT / ".conda/python.exe"), "-X", "utf8", "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        (INSTANCE / f"migration-{database}.log").write_text(
            result.stdout + result.stderr, encoding="utf-8"
        )
        if result.returncode:
            raise RuntimeError("迁移失败，保留已执行状态，不自动清空或覆盖数据库")
        engine = create_engine(
            dsn("root", credentials["root_password"], database), hide_parameters=True
        )
        with Session(engine) as session:
            initialize_policies(session)
            if database == "smartticket_ai":
                session.add(
                    User(
                        login="admin",
                        role="ADMIN",
                        password_hash=hash_password(credentials["admin_password"]),
                    )
                )
                session.flush()
                seed_simulation(session, credentials["demo_password"])
            session.commit()
        engine.dispose()
    with connection.cursor() as cursor:
        for name, key, database in (
            ("smartticket_app", "app_password", "smartticket_ai"),
            ("smartticket_test", "test_password", "smartticket_ai_test"),
        ):
            cursor.execute(
                f"CREATE USER '{name}'@'localhost' IDENTIFIED BY %s", (credentials[key],)
            )
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                (database,),
            )
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                permissions = (
                    "SELECT, INSERT"
                    if table
                    in (
                        "ticket_events",
                        "admin_events",
                        "knowledge_chunks",
                        "ai_feedback",
                        "model_analyses",
                        "review_collections",
                        "review_decisions",
                        "review_snapshots",
                    )
                    else "SELECT, INSERT, UPDATE"
                )
                if name == "smartticket_app" and table in (
                    "orders",
                    "products",
                    "order_items",
                    "payments",
                    "shipments",
                    "refunds",
                    "alembic_version",
                ):
                    permissions = "SELECT"
                cursor.execute(
                    f"GRANT {permissions} ON `{database}`.`{table}` TO '{name}'@'localhost'"
                )
    connection.close()
    env_file = ROOT / ".env"
    with env_file.open("x", encoding="utf-8") as handle:
        handle.write("SMARTTICKET_ENV=development\nSMARTTICKET_LLM_ENABLED=false\n")
        handle.write(
            "SMARTTICKET_DATABASE_URL="
            + dsn("smartticket_app", credentials["app_password"], "smartticket_ai")
            + "\n"
        )
        handle.write("SMARTTICKET_JWT_SECRET=" + credentials["jwt_secret"] + "\n")
    subprocess.run(
        ["icacls", str(env_file), "/inheritance:r", "/grant:r", f"{account}:F"],
        check=True,
        capture_output=True,
    )
    (INSTANCE / "login-info.txt").write_text(
        "SmartTicket AI — fictional local demo only\nURL: http://127.0.0.1:8010\n"
        + "admin / "
        + credentials["admin_password"]
        + "\n"
        + "demo_customer_1, demo_customer_2, demo_agent_1, demo_agent_2 / "
        + credentials["demo_password"]
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "provisioned",
                "port": PORT,
                "databases": ["smartticket_ai", "smartticket_ai_test"],
                "credentials": "saved privately; not printed",
                "original_mysql_3306": "untouched",
            }
        )
    )


if __name__ == "__main__":
    main()
