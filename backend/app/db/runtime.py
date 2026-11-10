from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app.core.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = None
        self.sessions = None
        if settings.database_url:
            url = settings.database_url.get_secret_value()
            options = {"pool_pre_ping": True, "hide_parameters": True}
            if url.startswith("sqlite"):
                options["connect_args"] = {"check_same_thread": False}
            else:
                options["isolation_level"] = "READ COMMITTED"
                options["connect_args"] = {
                    "charset": "utf8mb4",
                    "connect_timeout": 5,
                    "read_timeout": 10,
                    "write_timeout": 10,
                }
            self.engine = create_engine(url, **options)
            if self.engine.dialect.name == "sqlite":

                @event.listens_for(self.engine, "connect")
                def enforce_foreign_keys(connection, record):
                    connection.execute("PRAGMA foreign_keys=ON")

            self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def dispose(self):
        if self.engine:
            self.engine.dispose()
