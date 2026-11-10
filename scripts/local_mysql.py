"""Manage only the project's existing MySQL data directory, never a Windows service."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import pymysql

from scripts.provision_local_mysql import INSTANCE, MYSQL_HOME, PORT, own_server


def connect():
    credentials = json.loads((INSTANCE / "credentials.json").read_text())
    connection = pymysql.connect(
        host="127.0.0.1",
        port=PORT,
        user="root",
        password=credentials["root_password"],
        autocommit=True,
        connect_timeout=3,
    )
    own_server(connection)
    return connection


def start():
    try:
        with connect():
            print("Project MySQL already ready on 33307")
            return
    except pymysql.Error:
        pass
    state = json.loads((INSTANCE / "instance.json").read_text())
    data = (INSTANCE / "data").resolve()
    if data != Path(state["datadir"]).resolve() or not data.is_relative_to(INSTANCE.resolve()):
        raise RuntimeError("Project instance metadata mismatch")
    command = [
        str(MYSQL_HOME / "bin/mysqld.exe"),
        "--no-defaults",
        f"--basedir={MYSQL_HOME}",
        f"--datadir={data}",
        f"--port={PORT}",
        "--bind-address=127.0.0.1",
        "--mysqlx=0",
        f"--log-error={INSTANCE / 'mysql-error.log'}",
        f"--pid-file={INSTANCE / 'mysql.pid'}",
        "--default-time-zone=+00:00",
        "--character-set-server=utf8mb4",
        "--default-storage-engine=InnoDB",
        "--local-infile=0",
    ]
    with (INSTANCE / "process.log").open("a") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    state["pid"] = process.pid
    (INSTANCE / "instance.json").write_text(json.dumps(state, indent=2))
    for _ in range(90):
        try:
            with connect():
                print("Project MySQL ready on 33307")
                return
        except pymysql.Error:
            if process.poll() is not None:
                break
            time.sleep(1)
    raise RuntimeError("Project MySQL startup failed; inspect private logs")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "status", "stop"])
    action = parser.parse_args().action
    if action == "start":
        start()
    else:
        with connect() as connection:
            if action == "stop":
                with connection.cursor() as cursor:
                    cursor.execute("SHUTDOWN")
                print("Project MySQL graceful shutdown requested")
            else:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT @@port, VERSION()")
                    print(cursor.fetchone())


if __name__ == "__main__":
    main()
