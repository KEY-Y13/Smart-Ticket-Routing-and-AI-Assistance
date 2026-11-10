"""Isolated browser acceptance fixture. Never approves or changes business-database samples."""

import argparse
import json
import secrets
import socket
import subprocess
import sys
from datetime import UTC, datetime

import psutil
from sqlalchemy import select

from backend.app.core.config import Settings
from backend.app.core.security import hash_password
from backend.app.db.models import ReviewItem, ReviewSnapshot, User
from backend.app.db.runtime import Database
from backend.app.schemas.reviews import CollectionCreate, DecisionCreate
from backend.app.services import reviews
from nlp.artifacts import write_json
from nlp.baselines.train import train_baselines
from nlp.data.dataset import prepare_manifest
from scripts.provision_local_mysql import INSTANCE, ROOT, dsn

STATE = INSTANCE / "review-ui-fixture.json"
PORT = 8013


def settings_for(state):
    credentials = json.loads((INSTANCE / "credentials.json").read_text())
    return Settings(
        env="test",
        database_url=dsn("smartticket_test", credentials["test_password"], "smartticket_ai_test"),
        jwt_secret=state["jwt_secret"],
        artifact_root=ROOT / state["artifact_root"],
        llm_enabled=False,
        llm_call_budget=0,
        classifier_manifest=None,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "serve", "train", "stop"])
    action = parser.parse_args().action
    if action == "start":
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", PORT))
        stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S%f")
        state = {
            "login": "review-ui-" + stamp,
            "password": secrets.token_urlsafe(32),
            "jwt_secret": secrets.token_urlsafe(40),
            "collection_code": "review-ui-" + stamp,
            "artifact_root": ".mysql-local/review-ui-artifacts/" + stamp,
            "scope": "EXPLICIT_AUTOMATED_TEST_ONLY_NOT_HUMAN_REVIEW",
        }
        database = Database(settings_for(state))
        with database.sessions() as session:
            user = User(
                login=state["login"], password_hash=hash_password(state["password"]), role="ADMIN"
            )
            session.add(user)
            session.commit()
            source = ROOT / "data/samples/classification-smoke.jsonl"
            rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
            rows = [r for label in range(6) for r in [r for r in rows if r["label"] == label][:3]]
            collection = reviews.create_collection(
                session,
                user,
                CollectionCreate(
                    code=state["collection_code"],
                    title="隔离浏览器模拟评审（非真实人工审批）",
                    kind="CLASSIFICATION",
                    provenance=state["scope"],
                    items=rows,
                ),
                "ui-fixture-import",
            )
            records = list(
                session.scalars(
                    select(ReviewItem)
                    .where(
                        ReviewItem.collection_id == int(collection.id),
                    )
                    .order_by(ReviewItem.id)
                )
            )
            for item in records[1:]:
                reviews.decide(
                    session,
                    user,
                    item.id,
                    DecisionCreate(
                        expected_version=item.version,
                        decision="APPROVED",
                        personally_reviewed=True,
                        reason="隔离自动化浏览器测试预置模拟标注，禁止计为真实人工审核",
                        annotation={
                            "label": item.payload["label"],
                            "group_id": item.payload["group_id"],
                            "rights_status": "project_internal",
                        },
                    ),
                    "ui-fixture-simulated-review",
                )
            state.update(
                collection_id=str(collection.id),
                item_id=str(records[0].id),
                label=records[0].payload["label"],
            )
        database.dispose()
        STATE.write_text(json.dumps(state), encoding="utf-8")
        with (INSTANCE / "review-ui.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "-X", "utf8", "-m", "scripts.review_ui_fixture", "serve"],
                cwd=ROOT,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        state["pid"] = process.pid
        STATE.write_text(json.dumps(state), encoding="utf-8")
        print(
            json.dumps({"port": PORT, "database": "smartticket_ai_test", "fixture": state["scope"]})
        )
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if action == "serve":
        import uvicorn

        from backend.app.main import create_app

        uvicorn.run(create_app(settings_for(state)), host="127.0.0.1", port=PORT, access_log=False)
    elif action == "stop":
        process = psutil.Process(state["pid"])
        if (
            process.exe().lower() != str(ROOT / ".conda/python.exe").lower()
            or "scripts.review_ui_fixture" not in process.cmdline()
        ):
            raise RuntimeError("Refusing to stop an unrelated process")
        process.terminate()
        process.wait(timeout=15)
        print("Isolated browser API stopped; test rows retained.")
    else:
        settings = settings_for(state)
        database = Database(settings)
        with database.sessions() as session:
            snapshot = session.scalar(
                select(ReviewSnapshot)
                .where(
                    ReviewSnapshot.collection_id == int(state["collection_id"]),
                )
                .order_by(ReviewSnapshot.id.desc())
            )
            if snapshot is None or len(snapshot.rows) != 18:
                raise RuntimeError("Browser must freeze all 18 isolated fixture rows first")
            data = settings.artifact_root / "fixture.jsonl"
            data.parent.mkdir(parents=True, exist_ok=True)
            data.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in snapshot.rows) + "\n",
                encoding="utf-8",
            )
        split = settings.artifact_root / "split.json"
        write_json(split, prepare_manifest(data, "formal"))
        train_baselines(data, split, settings.artifact_root / "runs/browser-fixture/baselines")
        database.dispose()
        print("Trained isolated simulated-review fixture only; no business model approved.")


if __name__ == "__main__":
    main()
