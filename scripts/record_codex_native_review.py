"""Record actual Codex review of the original 72 Chinese examples; not generic approval."""

import json

from sqlalchemy import select

from backend.app.db.models import ReviewCollection, ReviewItem, User
from backend.app.services.ai_review_import import apply_offline
from nlp.artifacts import file_hash, write_json
from scripts.review_data_stage import ROOT, STAGE, Database, Settings

SOURCE_SHA = "98d791366f7819eb6546077876ad8d92f1a90c82dc3f5b3ad164304c5ba3d285"
# All 72 texts were individually read. Group 0's ninth question is about paying a final balance.
JUDGED_LABELS = [0] * 8 + [1] + [0] * 3 + [1] * 12 + [2] * 12 + [3] * 12 + [4] * 12 + [5] * 12


def main():
    source = ROOT / "data/samples/classification-smoke.jsonl"
    if file_hash(source) != SOURCE_SHA:
        raise RuntimeError("The actually reviewed source changed")
    originals = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    settings = Settings(_env_file=ROOT / ".env")
    db = Database(settings)
    try:
        with db.sessions() as session:
            collection = session.scalar(
                select(ReviewCollection).where(
                    ReviewCollection.code == "reference-classification-v1"
                )
            )
            items = {
                row.item_key: row
                for row in session.scalars(
                    select(ReviewItem).where(ReviewItem.collection_id == collection.id)
                )
            }
            cases, decisions = [], []
            for source_row, label in zip(originals, JUDGED_LABELS, strict=True):
                item = items[source_row["sample_id"]]
                if item.payload["text"] != source_row["text"]:
                    raise RuntimeError("Imported candidate differs from reviewed source")
                case = {
                    "id": f"review-{item.id}",
                    "review_item_id": item.id,
                    "content_hash": item.content_hash,
                    "english": None,
                    "chinese": item.payload["text"],
                }
                cases.append(case)
                decisions.append(
                    {
                        "id": case["id"],
                        "label": label,
                        "translation_ok": True,
                        "reason": (
                            "Codex逐条开卷核对主诉：尾款入口属于支付，原订单标签存在分歧，保留原文并排除。"
                        )
                        if source_row["sample_id"] == "smoke-0-09"
                        else (
                            "Codex逐条阅读原生中文，按主要处理诉求核对六类业务规则；只确认分类，不作业务处理承诺。"
                        ),
                    }
                )
            path = STAGE / "codex-native-72-review.json"
            if not path.exists():
                write_json(
                    path,
                    {
                        "method": "AI_CODEX_CLASSIFICATION_REVIEW",
                        "reviewer_model": "Codex",
                        "source_sha256": SOURCE_SHA,
                        "blind": False,
                        "review": {
                            "kind": "CLASSIFICATION",
                            "input": cases,
                            "decisions": decisions,
                            "protocol": "fictional-ai-review-v1",
                            "human_review": False,
                        },
                    },
                )
            actor = session.scalar(select(User).where(User.login == "admin"))
            print(apply_offline(session, actor, settings, path))
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
