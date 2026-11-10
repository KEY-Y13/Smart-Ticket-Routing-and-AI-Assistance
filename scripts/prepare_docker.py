"""Generate private project-specific Compose credentials; never overwrite existing secrets."""

import os
import secrets
import subprocess
from pathlib import Path

from sqlalchemy.engine import URL

root = Path(__file__).resolve().parents[1]
target = root / ".docker-secrets"
target.mkdir(exist_ok=False)
if os.name == "nt":
    account = os.environ.get("USERDOMAIN", "") + "\\" + os.environ["USERNAME"]
    subprocess.run(
        ["icacls", str(target), "/inheritance:r", "/grant:r", f"{account}:(OI)(CI)F"],
        check=True,
        capture_output=True,
    )
values = {
    name: secrets.token_urlsafe(32)
    for name in (
        "root_password",
        "app_password",
        "admin_password",
        "demo_password",
        "jwt_secret",
        "retrieval_secret",
    )
}
for user, password, name in (
    ("root", "root_password", "migration_database_url"),
    ("smartticket_app", "app_password", "app_database_url"),
):
    values[name] = URL.create(
        "mysql+pymysql",
        username=user,
        password=values[password],
        host="db",
        port=3306,
        database="smartticket_ai",
    ).render_as_string(hide_password=False)
for name, value in values.items():
    (target / name).write_text(value, encoding="utf-8")
print("Compose secrets saved privately. Existing local MySQL credentials are independent.")
